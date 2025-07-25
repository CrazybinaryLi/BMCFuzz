import re
import os
import shutil
import logging
import csv
import subprocess
import psutil

from datetime import datetime

MAX_COVER_POINTS = 0

NOOP_HOME = os.getenv("NOOP_HOME")
BMCFUZZ_HOME = os.getenv("BMCFUZZ_HOME")

def log_init(path=None):
    if path is None:
        current_dir = os.path.dirname(os.path.realpath(__file__))
    else:
        current_dir = path
        
    if not os.path.exists(os.path.join(current_dir, "logs")):
        os.makedirs(os.path.join(current_dir, "logs"))
    if not os.path.exists(os.path.join(current_dir, "logs", "fuzz")):
        os.makedirs(os.path.join(current_dir, "logs", "fuzz"))
    log_file_name = os.path.join(current_dir, "logs", datetime.now().strftime("%Y-%m-%d_%H-%M") + ".log")
    logging.basicConfig(filename=log_file_name, level=logging.INFO, format='%(asctime)s - %(message)s')
    log_message(f"Log initialized in {log_file_name}.")

def log_message(message, print_message=True):
    logging.info(message)
    if print_message:
        print(message)

def clear_logs(path=None):
    if path is None:
        current_dir = os.path.dirname(os.path.realpath(__file__))
    else:
        current_dir = path

    logs_dir = os.path.join(current_dir, "logs")
    if os.path.exists(logs_dir):
        shutil.rmtree(logs_dir)
    os.makedirs(logs_dir)
    fuzz_log_dir = os.getenv("FUZZ_LOG")
    os.makedirs(fuzz_log_dir, exist_ok=True)

def reset_terminal():
    try:
        subprocess.run(["stty", "sane"], check=True)
        log_message("reset terminal")
    except Exception as e:
        log_message(f"reset terminal error: {e}")

def run_command(command, shell=False):
    try:
        process = subprocess.Popen(command, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        return_code = process.wait()
    except KeyboardInterrupt:
        log_message("Process interrupted, terminating")
        kill_process_and_children(process.pid)
        reset_terminal()
        return_code = -1
    except Exception as e:
        log_message(f"Error: {e}")
        kill_process_and_children(process.pid)
        reset_terminal()
        return_code = -1
    finally:
        log_message("Closing process: " + command, print_message=False)
        return return_code

def kill_process_and_children(pid):
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            child.terminate()
        parent.terminate()

        gone, still_alive = psutil.wait_procs([parent] + children, timeout=5)
        for p in still_alive:
            p.kill()
        log_message("All processes killed")
    except psutil.NoSuchProcess:
        log_message("No such process")

def generate_rtl_files(run_snapshot, cpu, cover_type, mode):
    # get environment variables
    cover_tasks_path = str(os.getenv("COVER_POINTS_OUT"))
    os.makedirs(cover_tasks_path, exist_ok=True)
    rtl_init_dir = os.path.join(BMCFUZZ_HOME, "SetInitValues")
    rtl_src_dir = os.path.join(BMCFUZZ_HOME, "Formal", "demo", f"{cpu}")
    rtl_dst_dir = os.path.join(BMCFUZZ_HOME, "Formal", "coverTasks", "rtl")

    if os.path.exists(cover_tasks_path):
        shutil.rmtree(cover_tasks_path)
    os.makedirs(cover_tasks_path)
    os.makedirs(rtl_dst_dir)
    
    if not os.path.exists(rtl_src_dir):
        log_message(f"RTL source directory {rtl_src_dir} not found.")
        return
    with os.scandir(rtl_src_dir) as entries:
        for entry in entries:
            if entry.name.endswith(".v") or entry.name.endswith(".sv"):
                if run_snapshot:
                    if entry.name == "SimTop.sv":
                        init_file_path = os.path.join(rtl_init_dir, "SimTop_init.sv")
                        shutil.copy(init_file_path, rtl_dst_dir)
                        continue
                    if entry.name == "MemRWHelper.v":
                        init_file_path = os.path.join(rtl_init_dir, "MemRWHelper_formal.v")
                        shutil.copy(init_file_path, rtl_dst_dir)
                        continue
                shutil.copy(entry.path, rtl_dst_dir)
    
    cover_points_name = parse_and_modify_rtl_files(run_snapshot, cpu, cover_type, mode)

    log_message("Generated RTL files.")
    
    return cover_points_name

def parse_and_modify_rtl_files(run_snapshot, cpu, cover_type, mode):
    # cover name to cover id
    rtl_dir = os.path.join(BMCFUZZ_HOME, "Formal", "demo", f"{cpu}")
    rtl_dir = rtl_dir
    cover_name_file = rtl_dir + "/firrtl-cover.cpp"
    cover_points_name = []
    with open(cover_name_file, 'r') as file:
        lines = file.readlines()
        cover_name_begin = re.compile(r"static const char \*\w+_NAMES\[\] = {")
        cover_name_end = re.compile(r'};')
        cover_name_pattern = re.compile(r'\"(.*)\"')
        cover_name_flag = False
        for line in lines:
            cover_name_match = cover_name_begin.search(line)
            if cover_name_match:
                cover_name_flag = True
                continue
            if cover_name_flag:
                if cover_name_end.search(line):
                    break
                cover_name_match = cover_name_pattern.search(line)
                if cover_name_match:
                    module_name = cover_name_match.group(1).split(".")[0]
                    signal_name = cover_name_match.group(1).split(".")[1:]
                    signal_name = ".".join(signal_name)
                    # log_message(f"module_name: {module_name}, signal_name: {signal_name}", False)
                    cover_points_name.append((module_name, signal_name))

    rtl_dir = os.path.join(BMCFUZZ_HOME, "Formal", "coverTasks", "rtl")
    
    if run_snapshot:
        log_message("modify reg_reset in FormalTop.sv")
        rtl_file = rtl_dir + "/SimTop_init.sv"
        formal_top_file = rtl_dir + "/FormalTop.sv"
        with open(formal_top_file, 'r') as file:
            lines = file.readlines()
            for index, line in enumerate(lines):
                if line.startswith("reg reg_reset"):
                    lines[index] = "reg reg_reset = 1'b0;\n"
                    break
        with open(formal_top_file, 'w') as file:
            file.writelines(lines)
    else:
        rtl_file = rtl_dir + "/SimTop.sv"

    with open(rtl_file, 'r') as file:
        lines = file.readlines()
    os.remove(rtl_file)

    # multiclock -> glb_clk
    # if cpu == "rocket" or cpu == "boom":
    log_message("change multiclock to glb_clk")
    clock_pattern = re.compile(r'\(posedge (\w+)\)|\(posedge (\w+) or')
    for index, line in enumerate(lines):
        clock_match = clock_pattern.search(line)
        if clock_match:
            if clock_match.group(1) is not None:
                pre_clock = clock_match.group(1)
            else:
                pre_clock = clock_match.group(2)
            # log_message(f"clock_match: {pre_clock}", False)
            lines[index] = lines[index].replace(pre_clock, 'glb_clk')
            # lines[index] = re.sub(clock_pattern, '(posedge glb_clk)', line)
    
    new_lines = []
    cover_block_begin_pattern = re.compile(f'GEN_w(\d+)_{cover_type}.*{cover_type}_(\d+)')
    cover_block_end_pattern = re.compile(f'\);')
    cover_block_reset_pattern = re.compile(r"\.reset\((.*)\),")
    cover_block_valid_pattern = re.compile(r"\.valid\((.*)\)")
    cover_block_match = False
    cover_block_index = 0
    cover_block_len = 0
    for index, line in enumerate(lines):
        new_lines.append(line)
        cover_block_begin_match = cover_block_begin_pattern.search(line)
        if cover_block_begin_match:
            cover_block_match = True
            cover_block_len = int(cover_block_begin_match.group(1))
            cover_block_index = int(cover_block_begin_match.group(2))
        if cover_block_match:
            cover_block_reset_match = re.search(cover_block_reset_pattern, line)
            if cover_block_reset_match:
                cover_block_reset = cover_block_reset_match.group(1)
            cover_block_valid_match = re.search(cover_block_valid_pattern, line)
            if cover_block_valid_match:
                cover_block_valid = cover_block_valid_match.group(1)
            if re.search(cover_block_end_pattern, line):
                cover_block_match = False
                new_lines.append("  always @(posedge glb_clk) begin\n")
                new_lines.append(f"    if (!{cover_block_reset}) begin\n")
                if cover_block_len > 1:
                    for i in range(cover_block_len):
                        if mode == "smt":
                            new_lines.append(f"      cov_count_{cover_block_index+i}: cover({cover_block_valid}[{i}]);\n")
                        elif mode == "sat":
                            new_lines.append(f"      cov_count_{cover_block_index+i}: assert(~{cover_block_valid}[{i}]);\n")
                else:
                    if mode == "smt":
                        new_lines.append(f"      cov_count_{cover_block_index}: cover({cover_block_valid});\n")
                    elif mode == "sat":
                        new_lines.append(f"      cov_count_{cover_block_index}: assert(~{cover_block_valid});\n")
                new_lines.append("    end\n")
                new_lines.append("  end\n")

    if not run_snapshot:
        log_message("initial assume for reg")
        lines = []
        reg_cnt = 0 
        muti_reg_cnt = 0
        reg_pattern = re.compile(r"reg\s*(\[\d+:\d+\])?\s+(\w+)(\s*=\s*[^;]+)?;")
        muti_reg_pattern = re.compile(r"reg\s*(\[\d+:\d+\])?\s+(\w+)\s*\[(\d+):(\d+)\];")
        for line in new_lines:
            lines.append(line)
            reg_match = reg_pattern.search(line)
            if reg_match:
                if "RAND" in reg_match.group(2):
                    # log_message(f"skip RAND reg: {reg_match.group(2)}", False)
                    continue
                reg_cnt += 1
                # log_message(f"reg_name: {reg_match.group(2)}", False)
                reg_name = reg_match.group(2)
                if reg_match.group(3):
                    # log_message(f"skip reg with init value: {reg_match.group(2)}, init_value: {reg_match.group(3)}", False)
                    continue
                lines.append(f"  initial assume(!{reg_name});\n")
            muti_reg_match = muti_reg_pattern.search(line)
            if muti_reg_match:
                muti_reg_cnt += 1
                reg_name = muti_reg_match.group(2)
                reg_number = int(muti_reg_match.group(4)) - int(muti_reg_match.group(3)) + 1
                if reg_number > 16:
                    # log_message(f"skip muti_reg with reg_number > 16: {reg_name}, reg_number: {reg_number}", False)
                    continue
                # log_message(f"muti_reg_name: {reg_name}, reg_number: {reg_number}", False)
                for i in range(int(muti_reg_match.group(4)), int(muti_reg_match.group(3)) - 1, -1):
                    lines.append(f"  initial assume(!{reg_name}[{i}]);\n")
        new_lines = lines
        log_message(f"reg_cnt: {reg_cnt}\tmuti_reg_cnt: {muti_reg_cnt}")
    
    with open(rtl_file, 'w') as new_file:
        new_file.writelines(new_lines)
    
    set_max_cover_points(len(cover_points_name))

    return cover_points_name

def generate_sby_files(cover_points, cpu, mode):
    rtl_dir = os.path.join(BMCFUZZ_HOME, "Formal", "coverTasks", "rtl")
    cover_tasks_path = str(os.getenv("COVER_POINTS_OUT"))
    sby_template = str(os.getenv("SBY_TEMPLATE"))

    with open(sby_template, 'r') as template_file:
        template_content = template_file.read()
    
    rtl_files = [os.path.join(rtl_dir, file) for file in os.listdir(rtl_dir)]

    formal_files = '\n'.join([f"read -formal {os.path.basename(file)}" for file in rtl_files])
    verilog_files = '\n'.join([file for file in rtl_files])

    if cpu == "nutshell":
        default_depth = 90
        default_timeout = 2 * 60 * 60
    elif cpu == "rocket":
        default_depth = 75
        default_timeout = 3 * 60 * 60
    elif cpu == "boom":
        default_depth = 75
        default_timeout = 4 * 60 * 60
    else:
        default_depth = 50
        default_timeout = 1 * 60 * 60
    default_timeout = int(default_timeout)

    if mode == "smt":
        sby_mode = "cover"
        engine = "smtbmc bitwuzla"
    elif mode == "sat":
        sby_mode = "bmc"
        engine = "aiger rIC3"

    for cover_id in cover_points:
        if cover_id >= MAX_COVER_POINTS:
            log_message(f"cover_id: {cover_id} >= MAX_COVER_POINTS: {MAX_COVER_POINTS}")
            return

        cover_label = f"cov_count_{cover_id}"

        if mode == "smt":
            scripts = f"chformal -remove -cover c:{cover_label} %n\n"
        elif mode == "sat":
            scripts = f"chformal -remove -assert c:{cover_label} %n\n"

        sby_file_content = template_content.format(
            mode=sby_mode,
            depth=default_depth,
            timeout=default_timeout,
            engines=engine,
            formal_files=formal_files,
            top_module_name="FormalTop",
            scripts=scripts,
            cover_label=cover_label,
            verilog_files=verilog_files
        )
        
        sby_file_name = os.path.join(cover_tasks_path, f"cover_{cover_id}.sby")
        with open(sby_file_name, 'w') as sby_file:
            sby_file.write(sby_file_content)
    
    log_message("Generated .sby files.")

def set_max_cover_points(max_cover_points):
    global MAX_COVER_POINTS
    MAX_COVER_POINTS = max_cover_points

def clean_cover_files():
    cover_points_path = str(os.getenv("COVER_POINTS_OUT"))
    if not os.path.exists(cover_points_path):
        log_message(f"Cover points path {cover_points_path} not found.")
        return
    
    with os.scandir(cover_points_path) as entries:
        for entry in entries:
            if entry.name.startswith("cover_"):
                if entry.is_file():
                    os.remove(entry.path)
                else:
                    shutil.rmtree(entry.path)
            elif entry.name == "hexbin":
                shutil.rmtree(entry.path)
                os.mkdir(entry.path)
    
    log_message("Cleaned cover files.")

def generate_empty_cover_points_file(cover_num=0):
    cover_points_out = str(os.getenv("COVER_POINTS_OUT"))
    cover_points_file_path = cover_points_out + "/cover_points.csv"
    
    if os.path.exists(cover_points_file_path):
        os.remove(cover_points_file_path)
        
    set_max_cover_points(cover_num)
    with open(cover_points_file_path, mode='w', newline='', encoding='utf-8') as file:
        field_name = ['Index', 'Covered']
        csv_writer = csv.DictWriter(file, fieldnames=field_name)
        csv_writer.writeheader()

        for i in range(MAX_COVER_POINTS):
            csv_writer.writerow({'Index': i, 'Covered': 0})

if __name__ == "__main__":
    os.chdir(NOOP_HOME)
    clear_logs()
    log_init()
    clean_cover_files()

    # sample_cover_points = [1939, 8826]
    sample_cover_points = [5886]
    # sample_cover_points = [533, 2549, 1470, 1236, 941, 1816, 1587, 2174, 2446, 1004]

    run_snapshot = False
    snapshot_id = 0
    cpu = "rocket"
    # cpu = "boom"
    cover_type = "toggle"
    solver_mode = "sat"
    snapshot_file = os.path.join(BMCFUZZ_HOME, "SetInitValues", "csr_snapshot", f"{snapshot_id}")

    generate_rtl_files(run_snapshot, cpu, cover_type, solver_mode)
    generate_sby_files(sample_cover_points, cpu, solver_mode)
