import os
import re
import subprocess
import shutil
import csv
import json

from concurrent.futures import ThreadPoolExecutor, as_completed
from Verilog_VCD.Verilog_VCD import parse_vcd
from tqdm import tqdm
import time

from Tools import *

class Executor:
    run_snapshot = False
    snapshot_id = 0
    snapshot_file = ""

    mode = ""

    cpu = ""
    cover_type = ""

    env_path = ""
    cover_tasks_dir = ""

    MAX_WORKERS = 250

    debug = False
    
    SIGNAL_MATCH_RULES = {
        'nutshell': [
            {'name': 'r_data',   'hier': 'FormalTop.dut.mem.rdata_mem.helper_0', 'role': 'data'},
            {'name': 'r_enable', 'hier': 'FormalTop.dut.mem.rdata_mem.helper_0', 'role': 'enable'},
            {'name': 'r_index',  'hier': 'FormalTop.dut.mem.rdata_mem.helper_0', 'role': 'addr'}
        ],
        'rocket': [
            {'name': 'r_data',   'hier': 'FormalTop.dut.mem.srams.mem.helper_0', 'role': 'data'},
            {'name': 'r_enable', 'hier': 'FormalTop.dut.mem.srams.mem.helper_0', 'role': 'enable'},
            {'name': 'r_index',  'hier': 'FormalTop.dut.mem.srams.mem.helper_0', 'role': 'addr'}
        ],
        'boom': [
            {'name': 'r_data',   'hier': 'FormalTop.dut.mem.srams.mem.helper_0', 'role': 'data'},
            {'name': 'r_enable', 'hier': 'FormalTop.dut.mem.srams.mem.helper_0', 'role': 'enable'},
            {'name': 'r_index',  'hier': 'FormalTop.dut.mem.srams.mem.helper_0', 'role': 'addr'}
        ],
    }

    def init(self, cpu, cover_type, run_snapshot, mode, debug=False):
        self.cpu = cpu
        self.cover_type = cover_type
        self.run_snapshot = run_snapshot
        self.env_path = str(os.getenv("OSS_CAD_SUITE_HOME"))
        self.cover_tasks_dir = str(os.getenv("COVER_POINTS_OUT"))
        self.mode = mode

        self.debug = debug

        log_message(f"try to load env: {self.env_path}")
        env_command = f"bash -c 'source {self.env_path} && env'"
        env_vars = run_command(env_command, shell=True)

        if env_vars != 0:
            log_message(f"env load failed: {env_vars}")
            exit(1)
    
    def set_snapshot_id(self, snapshot_id, snapshot_file):
        self.snapshot_id = snapshot_id
        self.snapshot_file = snapshot_file

    def run(self, cover_points):
        # os.chdir(output_dir)
        cover_cases = []
        strat_time = time.time()
        max_workers = min(self.MAX_WORKERS, os.cpu_count())
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self.execute_cover_task, cover): cover for cover in cover_points}
            with tqdm(total=len(futures), desc="Processing covers") as pbar:
                for future in as_completed(futures):
                    cover = futures[future]
                    try:
                        if future.result() != -1:
                            cover_cases.append(cover)
                        # print("result:", future.result())
                    except Exception as e:
                        log_message(f"cover_{cover} task failed: {e}")
                    pbar.update(1)
        end_time = time.time()
        log_message(f"All tasks completed, total time: {end_time - strat_time:.2f} seconds, found {len(cover_cases)} cases", print_message=False)

        return (cover_cases, end_time - strat_time)

    def execute_cover_task(self, cover):
        sby_command = f"source {self.env_path}"
        sby_path = os.path.join(BMCFUZZ_HOME, "sby", "sbysrc", "sby.py")
        if self.mode == "smt":
            sby_command += f" && {sby_path} -f {self.cover_tasks_dir}/cover_{cover}.sby"
        elif self.mode == "sat":
            rIC3_path = os.path.join(BMCFUZZ_HOME, "Formal", "bin", "rIC3")
            sby_command += f" && {sby_path} -f {self.cover_tasks_dir}/cover_{cover}.sby --rIC3 {rIC3_path}"
        sby_command = f"bash -c '{sby_command}'"
        st_time = time.time()
        return_code = run_command(sby_command, shell=True)
        ed_time = time.time()
        log_message(f"cover_{cover} sby finished, time: {ed_time - st_time:.2f} seconds, return code: {return_code}", print_message=False)

        cover_point = -1
        cover_dir = os.path.join(self.cover_tasks_dir, f"cover_{cover}")
        self.parse_log_file(cover, cover_dir)
        pass_code = 0
        if self.mode == "sat":
            pass_code = 2
        if return_code == pass_code:
            log_message(f"found case: cover_{cover}", print_message=False)
            v_file_path = os.path.join(cover_dir, "engine_0", "trace0_tb.v")
            vcd_file_path = os.path.join(cover_dir, "engine_0", "trace0.vcd")
            witness_file_path = os.path.join(cover_dir, "engine_0", "trace0_aiw.yw")
            hexbin_dir = os.path.join(self.cover_tasks_dir, "hexbin")
            if os.path.exists(witness_file_path) or os.path.exists(vcd_file_path):
                # self.parse_v_file(cover, v_file_path, hexbin_dir)
                if self.mode == "smt":
                    log_message(f"parse vcd file: {vcd_file_path}", print_message=False)
                    self.parse_vcd_file(cover, vcd_file_path, hexbin_dir)
                    self.generate_footprint(cover, hexbin_dir, src_format="bin")
                elif self.mode == "sat":
                    log_message(f"parse witness file: {witness_file_path}", print_message=False)
                    # self.parse_vcd_file(cover, vcd_file_path, hexbin_dir)
                    # self.generate_footprint(cover, hexbin_dir, src_format="bin")
                    self.parse_witness_file(cover, witness_file_path, hexbin_dir)
                    self.generate_memory(cover, hexbin_dir, src_format="witness", dst_format="bin")
                    # self.generate_memory(cover, hexbin_dir, src_format="witness", dst_format="footprints")
                cover_point = cover
            else:
                log_message(f"No BMC output!", print_message=False)
        else:
            log_message(f"case({cover}) not covered, return code: {return_code}", print_message=False)
        
        if not self.debug:
            if os.path.exists(f"{self.cover_tasks_dir}/cover_{cover}.sby"):
                os.remove(f"{self.cover_tasks_dir}/cover_{cover}.sby")
            if os.path.exists(f"{self.cover_tasks_dir}/cover_{cover}"):
                shutil.rmtree(f"{self.cover_tasks_dir}/cover_{cover}")
        
        return cover_point
        
    def parse_log_file(self, cover_no, cover_dir):
        cover_log_path = os.path.join(cover_dir, "logfile.txt")
        if self.mode == "smt":
            check_log_pattern = r"Checking cover reachability in step (\d+).."
        elif self.mode == "sat":
            check_log_pattern = r"bmc depth: (\d+)"
        summary_log_pattern = r"summary: Elapsed clock time \[H:MM:SS \(secs\)\]: (\d+:\d+:\d+) \((\d+\))"
        return_log_pattern = r"DONE \((\S+), rc=(\d+)\)"
        with open(cover_log_path, 'r') as log_file:
            lines = log_file.readlines()
            for line in reversed(lines):
                check_match = re.search(check_log_pattern, line)
                summary_match = re.search(summary_log_pattern, line)
                return_match = re.search(return_log_pattern, line)
                if check_match:
                    cover_step = check_match.group(1)
                    break
                if summary_match:
                    cover_time = summary_match.group(1)
                if return_match:
                    cover_return = (return_match.group(1), return_match.group(2))
            log_message(f"cover:{cover_no}, time:{cover_time}, step:{cover_step}, return:({cover_return[0]}, rc={cover_return[1]})")

    def data_parser(self, addr, data):
        addr = int(addr, 2) * 8

        addr_hex = f"{addr:#010x}"
        lower_32 = int(data[32:], 2)
        lower_32_hex = f"{lower_32:#010x}"
        upper_32 = int(data[:32], 2)
        upper_32_hex = f"{upper_32:#010x}"
        log_message(f"Address: {addr_hex}, Data: {lower_32_hex} {upper_32_hex}", print_message=False)
        
        data = int(data, 2).to_bytes(8, byteorder='little')

        return (addr, data)
    
    def bin_file_builder(self, memory_map, output_file_path):
        with open(output_file_path, 'wb') as output_file:
            current_addr = 0
            for addr in sorted(memory_map.keys()):
                if current_addr < addr:
                    gap_size = addr - current_addr
                    log_message(f"Filling gap of {gap_size} bytes from {current_addr:#010x} to {addr:#010x}", print_message=False)
                    output_file.write(b'\x00' * gap_size)
                    current_addr = addr
                output_file.write(memory_map[addr])
                current_addr += 8
        log_message(f"parse and save bin file: {output_file_path}", print_message=False)

    def parse_vcd_file(self, cover_no, vcd_file_path, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, f"cover_{cover_no}.bin")
        
        signal_rule = self.SIGNAL_MATCH_RULES[self.cpu]
        vcd_data = parse_vcd(vcd_file_path)

        memory_map = {}

        signal_values = {'enable': [], 'addr': {}, 'data': {}}
        for netinfo in vcd_data.values():
            for signal in signal_rule:
                if (
                    netinfo['nets'][0]['name'] == signal['name'] and
                    netinfo['nets'][0]['hier'] == signal['hier']
                ):
                    for time_sig in netinfo['tv']:
                        clock = time_sig[0]
                        value = time_sig[1]
                        if signal['role'] == 'enable':
                            signal_values[signal['role']].append((int(clock), value))
                        else:
                            signal_values[signal['role']][int(clock)] = value
                        # log_message(f"{signal['role']}: {clock}, {value}")
        # log_message(f"enable: {signal_values['enable']}")
        signal_values['enable'] = sorted(signal_values['enable'], key=lambda x: x[0])
        for index, (clock, value) in enumerate(signal_values['enable']):
            if value == '1' and index+1 < len(signal_values['enable']):
                next_clock = signal_values['enable'][index + 1][0]
                addr = signal_values['addr'].get(clock)
                data = signal_values['data'].get(next_clock)
                # log_message(f"enable: {clock}, addr: {addr}, data: {next_clock}, {data}")
                addr, data = self.data_parser(addr, data)
                memory_map[addr] = data
        
        self.bin_file_builder(memory_map, output_file_path)
            
    def parse_v_file(self, cover_no, v_file_path, output_dir):
        pattern = r"\.helper_0\.memory\[(29'b[01]+)\] = (64'b[01]+);"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, f"cover_{cover_no}.bin")

        memory_map = {}

        with open(v_file_path, 'r') as v_file:
            for line in v_file:
                match = re.search(pattern, line)
                if match:
                    data_addr = match.group(1).split("'b")[1]
                    data_bin = match.group(2).split("'b")[1]
                    addr, data = self.data_parser(data_addr, data_bin)
                    memory_map[addr] = data
        
        self.bin_file_builder(memory_map, output_file_path)
    
    def parse_witness_file(self, cover_no, witness_file_path, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        witness_output_path = os.path.join(output_dir, f"cover_{cover_no}.witness")

        display_witness_command = f"source {self.env_path} && yosys-witness display {witness_file_path} > {witness_output_path}"
        display_witness_command = f"bash -c '{display_witness_command}'"
        run_command(display_witness_command, shell=True)

        step_data = []
        with open(witness_output_path, 'r') as witness_file:
            lines = witness_file.readlines()
            for line in lines:
                if "rand_value" in line:
                    step_data.append(line.strip().split(" ")[-1])

        if not self.run_snapshot:
            step_data = step_data[2:]
                
        with open(witness_output_path, 'w') as f:
            steps = len(step_data)
            f.write(str(steps) + "\n")
            step = 0
            for bits in step_data:
                step += 1
                # bits = data['bits'][:64]
                upper_32 = bits[:32]
                lower_32 = bits[32:]

                hex_bits = f"{int(bits, 2):#018x}"
                upper_32 = f"{int(upper_32, 2):#010x}"
                lower_32 = f"{int(lower_32, 2):#010x}"

                f.write(f"{hex_bits}\n")
                
                if int(bits, 2) != 0 and int(bits, 2) != 0xFFFFFFFFFFFFFFFF:
                    log_message(f"Step {step}: {lower_32} {upper_32}", print_message=False)
                    # log_message(f"bits: {bits}")

    def generate_memory(self, cover_point, output_dir, src_format="bin", dst_format="footprints"):
        if src_format == "bin":
            src_file_path = os.path.join(output_dir, f"cover_{cover_point}.bin")
        elif src_format == "witness":
            src_file_path = os.path.join(output_dir, f"cover_{cover_point}.witness")
        if dst_format == "footprints":
            dst_file_path = os.path.join(output_dir, f"cover_{cover_point}.footprints")
        elif dst_format == "bin":
            dst_file_path = os.path.join(output_dir, f"cover_{cover_point}.bin")
        if src_format == dst_format:
            log_message(f"the same format, no need to convert: {src_file_path} -> {dst_file_path}", print_message=False)
            return 0
        log_file_path = os.path.join(output_dir, f"cover_{cover_point}.log")

        commands = f"cd {NOOP_HOME} && source env.sh && ./build/fuzzer"
        commands += f" --auto-exit"
        commands += f" -c firrtl.{self.cover_type}"
        commands += f" -- {src_file_path}"
        if src_format == "witness":
            commands += f" --as-witness"
        commands += f" -I 300"
        commands += f" -C 3000"
        commands += f" --fuzz-id 0"
        commands += f" --no-diff"
        if self.run_snapshot:
            commands += " --run-snapshot"
            commands += f" --load-snapshot {self.snapshot_file}"
        if dst_format == "bin":
            commands += f" --dump-linearized {dst_file_path}"
        elif dst_format == "footprints":
            commands += f" --dump-footprints {dst_file_path}"
        # commands += f" > dev/null 2>&1"
        commands += f" > {log_file_path} 2>&1"
        commands = "bash -c '" + commands + "'"

        ret = run_command(commands, shell=True)
        if not self.debug:
            os.remove(f"{src_file_path}")
            os.remove(f"{log_file_path}")
        log_message(f"generate memory file: {dst_file_path}", print_message=False)

        with open(dst_file_path, "r+b") as f:
            data = f.read()
            if len(data) == 0:
                data = b"\x13\x00\x00\x00"
                f.write(data)
                log_message(f"empty memory file, write default data: {data}", print_message=False)

        return 0

if __name__ == "__main__":
    os.chdir(NOOP_HOME)
    clear_logs()
    log_init()
    clean_cover_files()

    # sample_cover_points = [1939, 8826]
    # sample_cover_points = [14350]
    sample_cover_points = [2730, 623]
    # sample_cover_points = [533, 2549, 1470, 1236, 941, 1816, 1587, 2174, 2446, 1004]

    run_snapshot = True
    # run_snapshot = False
    snapshot_id = 0
    # cpu = "nutshell"
    cpu = "rocket"
    # cpu = "boom"
    cover_type = "toggle"
    # cover_type = "line"
    # cover_type = "control"
    solver_mode = "sat"
    snapshot_file = os.path.join(BMCFUZZ_HOME, "SetInitValues", "csr_snapshot", f"{snapshot_id}")

    generate_rtl_files(run_snapshot, cpu, cover_type, solver_mode)
    generate_sby_files(sample_cover_points, cpu, solver_mode)

    executor = Executor()
    executor.init(cpu, cover_type, run_snapshot, solver_mode, debug=True)
    executor.set_snapshot_id(snapshot_id, snapshot_file)
    cover_cases, execute_time = executor.run(sample_cover_points)
    print(f"total covered cases: {len(cover_cases)}, time cost: {execute_time:.6f} s")
    print("cover_cases:", cover_cases)
    # v_file_path = os.path.join(BMCFUZZ_HOME, "Formal", "coverTasks", "cover_9226", "engine_0", "trace0_tb.v")
    # vcd_file_path = os.path.join(BMCFUZZ_HOME, "Formal", "coverTasks", "cover_9226", "engine_0", "trace0.vcd")
    # witness_file_path = os.path.join(BMCFUZZ_HOME, "Formal", "coverTasks", "cover_9226", "engine_0", "trace0_aiw.yw")
    # output_dir = os.path.join(BMCFUZZ_HOME, "Formal", "coverTasks", "hexbin")
    # executor.parse_v_file(9226, v_file_path, output_dir)
    # executor.parse_vcd_file(9226, vcd_file_path, output_dir)
    # executor.parse_witness_file(9226, witness_file_path, output_dir)
    generate_empty_cover_points_file()
