import os
import sys
import time
import shutil
import argparse
import subprocess


sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "Formal"))
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "SetInitValues"))

from datetime import datetime

from SetInitValues.split_sv_mudules import split_sv_modules
from SetInitValues.generate_hierarchy import hierarchy_yaml_parser
from SetInitValues.json_add_initval import add_regs
from SetInitValues.vcd_parser import vcd_to_json
from SetInitValues.connect_reginit_vcd_parser import connect_json_vcd, update_other_rtl
from SetInitValues.new_init_folder import create_init_files

from SetInitValues.CSRTransitionSelect import CSRTransitionSelect

from Formal.Scheduler import Scheduler, FuzzArgs
from Formal.Tools import log_message, clear_logs, log_init, run_command

NOOP_HOME = os.getenv("NOOP_HOME")
BMCFUZZ_HOME = os.getenv("BMCFUZZ_HOME")

class BMCFuzz:
    cpu = None
    
    split_sv_modules_dir = None
    module_with_regs_json = None

    formal_dir = None
    set_init_values_dir = None
    csr_wave_dir = None

    csr_transition_selector = None

    scheduler = None

    cover_type = None

    # debug
    test_coverage = False
    
    def init(self, cpu, cover_type, special_wave=False):
        self.cpu = cpu
        self.cover_type = cover_type
        
        current_dir = os.path.dirname(os.path.realpath(__file__))

        set_init_values_dir = os.path.join(current_dir, 'SetInitValues')
        formal_dir = os.path.join(current_dir, 'Formal')

        self.set_init_values_dir = set_init_values_dir
        self.formal_dir = formal_dir

        """ Set Init Values """
        log_message("Start Set Init Values")
        if os.path.exists(set_init_values_dir+'/SimTop.sv'):
            os.remove(set_init_values_dir+'/SimTop.sv')
        shutil.copyfile(set_init_values_dir+f'/rtl_src/{cpu}/SimTop_{cover_type}.sv', set_init_values_dir+'/SimTop.sv')
        default_sv_file = os.path.join(set_init_values_dir, 'SimTop.sv')
        default_top_module_name = 'SimTop'

        # step1: split_sv_modules
        log_message(f"Step1: Split modules")
        split_sv_modules_dir = os.path.join(set_init_values_dir, default_top_module_name + '_split')
        if os.path.exists(split_sv_modules_dir):
            shutil.rmtree(split_sv_modules_dir)
        split_sv_modules(default_sv_file, split_sv_modules_dir)
        self.split_sv_modules_dir = split_sv_modules_dir
        log_message(f"Modules has been split into {split_sv_modules_dir} directory.")

        # step2: run_svinst to generate modules yaml
        log_message(f"Step2: Generate modules yaml")
        output_yaml = os.path.join(set_init_values_dir, default_top_module_name + '.yaml')
        command = f"{set_init_values_dir}/bin/svinst {default_sv_file} > {output_yaml}"
        if not os.path.exists(output_yaml):
            result = subprocess.run(command, shell=True, text=True, capture_output=True)
            if result.returncode != 0:
                log_message(f"Generate modules yaml command: {command} failed with return code: {result.returncode}")
                log_message(f"Error output: {result.stderr}")
                exit(result.returncode)
        log_message(f"Generate modules yaml command: {command} executed successfully.")

        # step3: hierarchy yaml parser
        log_message(f"Step3: Generate hierarchy json")
        output_json = os.path.join(set_init_values_dir, default_top_module_name + '.json')
        if os.path.exists(output_json):
            os.remove(output_json)
        result = hierarchy_yaml_parser(output_yaml, output_json, default_top_module_name)
        if result != 0:
            log_message(f"Generate hierarchy json failed with return code: {result}")
            exit(result)
        log_message(f"Generate hierarchy json executed successfully.")

        # step4: add regs init values
        log_message(f"Step4: Add regs init values")
        output_json_with_regs = os.path.join(set_init_values_dir, default_top_module_name + '_with_regs.json')
        add_regs(output_json, output_json_with_regs, split_sv_modules_dir)
        self.module_with_regs_json = output_json_with_regs
        log_message(f"Add regs init values executed successfully.")

        # set csr wave dir
        self.csr_wave_dir = os.path.join(set_init_values_dir, 'csr_wave')
        
        log_message("End Set Init Values")

        """ CSR Transition Select """
        log_message("Start CSR Transition Selector Init")
        self.csr_transition_selector = CSRTransitionSelect()
        if not special_wave:
            self.csr_transition_selector.file_init(cpu, cover_type)
        else:
            csr_wave_dir = os.path.join(set_init_values_dir, 'csr_wave')
            csr_snapshot_dir = os.path.join(set_init_values_dir, 'csr_snapshot')
            reset_wave_file = os.path.join(set_init_values_dir, f'rtl_src/{cpu}/reset_{cover_type}.vcd')
            reset_snapshot_file = os.path.join(set_init_values_dir, f'rtl_src/{cpu}/reset_snapshot')
            shutil.copyfile(reset_wave_file, os.path.join(csr_wave_dir, '0.vcd'))
            shutil.copyfile(reset_snapshot_file, os.path.join(csr_snapshot_dir, '0'))
        log_message("End CSR Transition Selector Init")

        """ Formal """
        log_message("Start Formal Init")
        self.scheduler = Scheduler()
        self.scheduler.init(cpu, cover_type, True)
        log_message("End Formal Init")

        """ clean errors&crashes """
        log_message("Clean Errors & Crashes")
        if os.path.exists(os.path.join(os.getenv("NOOP_HOME"), 'errors')):
            shutil.rmtree(os.path.join(os.getenv("NOOP_HOME"), 'errors'))
        os.mkdir(os.path.join(os.getenv("NOOP_HOME"), 'errors'))
        if os.path.exists(os.path.join(os.getenv("NOOP_HOME"), 'crashes')):
            shutil.rmtree(os.path.join(os.getenv("NOOP_HOME"), 'crashes'))
        os.mkdir(os.path.join(os.getenv("NOOP_HOME"), 'crashes'))
        
    def generate_init_file(self, wave_vcd_path):
        log_message("Start Run On Wave")
        log_message(f"Wave VCD Path: {wave_vcd_path}")
        
        # convert wave vcd to json
        wave_json_path = wave_vcd_path.replace('.vcd', '.json')
        if os.path.exists(wave_json_path):
            os.remove(wave_json_path)
        vcd_to_json(wave_vcd_path, wave_json_path, self.cpu)
        log_message(f"Convert wave vcd to json executed successfully.")

        # set regs init values
        updated_registers_json = os.path.join(self.set_init_values_dir, 'updated_registers.json')
        if os.path.exists(updated_registers_json):
            os.remove(updated_registers_json)
        result = connect_json_vcd(self.module_with_regs_json, wave_json_path, updated_registers_json)
        if result != 0:
            log_message(f"Set regs init values failed with return code: {result}")
            exit(result)
        log_message(f"Set regs init values executed successfully.")

        # create init files
        init_file_path = os.path.join(self.set_init_values_dir, 'SimTop_init.sv')
        init_dir_path = self.split_sv_modules_dir + '_init'
        if os.path.exists(init_dir_path):
            shutil.rmtree(init_dir_path)
        create_init_files(self.split_sv_modules_dir, self.split_sv_modules_dir + '_init', updated_registers_json, init_file_path)
        log_message(f"Create init files executed successfully.")

        # update other rtl files
        src_rtl_dir = os.path.join(self.set_init_values_dir, 'rtl_src', self.cpu)
        dst_rtl_dir = os.path.join(self.set_init_values_dir)
        update_other_rtl(src_rtl_dir, dst_rtl_dir, wave_json_path)
        log_message(f"Update other rtl files executed successfully.")

        log_message("End Run On Wave")

    def fuzz_init(self):
        # run on reset wave
        # reset_wave_file = os.path.join(self.csr_wave_dir, '0.vcd')
        # self.generate_init_file(reset_wave_file)
        
        # init fuzz log
        fuzz_log_dir = os.path.join(BMCFUZZ_HOME, 'logs')
        make_log_file = os.path.join(fuzz_log_dir, f'make_fuzzer.log')
        fuzz_log_file = os.path.join(fuzz_log_dir, f"fuzz_init.log")

        # init fuzz args
        fuzz_args = FuzzArgs()
        fuzz_args.cover_type = self.cover_type
        fuzz_args.max_runs = 3000

        fuzz_args.continue_on_errors = True
        # fuzz_args.insert_nop = True
        # fuzz_args.save_errors = True
        # fuzz_args.run_snapshot = True # use reset snapshot
        
        fuzz_args.max_cycle = 10000
        fuzz_args.max_instr = 10000

        fuzz_args.dump_csr = True
        fuzz_args.dump_wave = True

        fuzz_args.no_diff = self.test_coverage

        # fuzz_args.as_footprints = True
        if fuzz_args.as_footprints:
            fuzz_args.corpus_input = os.getenv("FOOTPRINTS_CORPUS")
        else:
            fuzz_args.corpus_input = os.getenv("LINEARIZED_CORPUS")

        fuzz_args.make_log_file = make_log_file
        fuzz_args.output_file = fuzz_log_file

        # make fuzzer and clean fuzz run dir
        fuzz_args.make_fuzzer()
        self.scheduler.clean_fuzz_run()
        
        # generate fuzz command and run
        fuzz_command = fuzz_args.generate_fuzz_command()
        fuzz_start_time = time.time()
        return_code = run_command(fuzz_command, shell=True)
        log_message(f"fuzz init time cost: {time.time() - fuzz_start_time:.2f}s")
        log_message(f"fuzz init return code: {return_code}")

        self.scheduler.update_coverage()
        
        self.csr_transition_selector.update()
    
    def run_hybrid_loop(self):
        loop_count = 0
        while(True):
            loop_count += 1
            log_message(f"Hybrid Loop {loop_count}")

            # run formal
            if not self.scheduler.run_formal():
                log_message(f"Hybrid Loop End: no more points to cover.")
                break
                
            # run snapshot fuzz
            self.scheduler.run_snapshot_fuzz()

            # update coverage
            self.scheduler.update_coverage()

            self.csr_transition_selector.update()

            self.scheduler.output_uncovered_points()
        
        # display stats
        self.scheduler.display_coverage()
        self.scheduler.output_uncovered_points()

        # restart init
        self.scheduler.restart_init()
    
    def run(self):
        # run fuzz init and generate csr transition waves
        self.fuzz_init()

        loop_count = 0
        while(True):
            loop_count += 1
            log_message(f"Snapshot Loop {loop_count}")
            
            # select highest score snapshot
            best_snapshot_id = self.csr_transition_selector.select_highest_score_snapshot()
            self.csr_transition_selector.output_transition_score()
            wave_path = os.path.join(self.csr_wave_dir, f'{best_snapshot_id}.vcd')
            if best_snapshot_id == -1:
                log_message(f"Exit: no more csr transitions.")
                break

            # generate init file
            self.generate_init_file(wave_path)
            # self.csr_transition_selector.delete_waveform(best_snapshot_id)
            # self.csr_transition_selector.delete_snapshot(best_snapshot_id)

            # make fuzzer
            fuzzer = FuzzArgs()
            fuzzer.cover_type = self.cover_type
            fuzzer.run_snapshot = True
            fuzzer.make_log_file = os.path.join(BMCFUZZ_HOME, 'logs', 'make_fuzzer.log')
            fuzzer.make_fuzzer()

            # run hybrid loop
            self.scheduler.set_snapshot_id(best_snapshot_id)
            self.run_hybrid_loop()
        
        log_message("End Snapshot Loop")
        self.scheduler.display_coverage()
    
def run(args):
    current_dir = os.path.dirname(os.path.realpath(__file__))
    formal_dir = os.path.join(current_dir, 'Formal')
    clear_logs(current_dir)
    clear_logs(formal_dir)
    log_init(current_dir)

    log_message("Sleep 10 seconds for background running")
    time.sleep(10)
    log_message("Start Running")

    fuzz = BMCFuzz()
    fuzz.init(cpu=args.cpu, cover_type=args.cover_type)
    fuzz.run()

def run_on_special_wave(args):
    current_dir = os.path.dirname(os.path.realpath(__file__))
    formal_dir = os.path.join(current_dir, 'Formal')
    clear_logs(current_dir)
    clear_logs(formal_dir)
    log_init(current_dir)

    fuzz = BMCFuzz()
    fuzz.init(cpu=args.cpu, cover_type=args.cover_type, special_wave=True)

    # generate init file
    fuzz.generate_init_file(os.path.join(fuzz.set_init_values_dir, 'csr_wave', str(args.wave_id)+'.vcd'))

    # run hybrid loop
    # fuzz.run_hybrid_loop(1)

def test(args):
    current_dir = os.path.dirname(os.path.realpath(__file__))
    clear_logs(current_dir)
    log_init(current_dir)

    fuzz = BMCFuzz()
    fuzz.init(cpu=args.cpu, cover_type=args.cover_type)

    if args.test_fuzz:
        log_message("Test Fuzz Init")
        fuzz.fuzz_init()

    if args.test_reset:
        log_message("Test Reset Snapshot")

        best_snapshot_id = 0
        wave_path = os.path.join(fuzz.csr_wave_dir, f'{best_snapshot_id}.vcd')
        fuzz.generate_init_file(wave_path)

        fuzzer = FuzzArgs()
        fuzzer.cover_type = fuzz.cover_type
        fuzzer.run_snapshot = True
        fuzzer.make_log_file = os.path.join(BMCFUZZ_HOME, 'logs', 'make_fuzzer.log')
        fuzzer.make_fuzzer()
        
        fuzz.scheduler.set_snapshot_id(best_snapshot_id)
        fuzz.run_hybrid_loop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    parser.add_argument("--fuzz", "-f", action='store_true', help="Run fuzz")
    parser.add_argument("--special-wave", "-s", action='store_true', help="Run on special wave")

    parser.add_argument("--test-fuzz", "-tf", action='store_true', help="Run test fuzz")
    parser.add_argument("--test-reset", "-tr", action='store_true', help="Run test reset snapshot")

    parser.add_argument("--cpu", type=str, default="rocket", help="CPU type")
    parser.add_argument("--cover-type", "-c", type=str, default="toggle", help="Cover type")

    parser.add_argument("--delete-yaml", "-d", action='store_true', help="Delete yaml")

    parser.add_argument("--wave-id", "-w", type=int, default=0, help="Wave id")

    args = parser.parse_args()

    if args.delete_yaml:
        yaml_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'SetInitValues', 'SimTop.yaml')
        if os.path.exists(yaml_path):
            os.remove(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'SetInitValues', 'SimTop.yaml'))
    
    if args.fuzz:
        run(args)
    if args.special_wave:
        run_on_special_wave(args)
    if args.test_fuzz or args.test_reset:
        test(args)

