/**
 * Copyright (c) 2023 Institute of Computing Technology, Chinese Academy of Sciences
 * xfuzz is licensed under Mulan PSL v2.
 * You can use this software according to the terms and conditions of the Mulan PSL v2.
 * You may obtain a copy of Mulan PSL v2 at:
 *          http://license.coscl.org.cn/MulanPSL2
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 * See the Mulan PSL v2 for more details.
 */
use std::path::PathBuf;

use crate::coverage::*;
use crate::harness;
use crate::monitor;

use libafl::prelude::*;
use libafl::schedulers::QueueScheduler;
use libafl::stages::StdMutationalStage;
use libafl::state::StdState;
use libafl::StdFuzzer;
use libafl_bolts::{current_nanos, rands::StdRand, tuples::tuple_list};

pub(crate) fn run_fuzzer(
    random_input: bool,
    max_iters: Option<u64>,
    corpus_input: Option<String>,
    corpus_output: Option<String>,
    continue_on_errors: bool,
    save_errors: bool,
) {
    // Scheduler, Feedback, Objective
    let scheduler = QueueScheduler::new();
    let observer =
        unsafe { StdMapObserver::from_mut_ptr("signals", cover_as_mut_ptr(), cover_len()) };
    let mut feedback = MaxMapFeedback::new(&observer);
    let mut objective = CrashFeedback::new();

    // State, Manager
    let mut state = StdState::new(
        StdRand::with_seed(current_nanos()),
        InMemoryCorpus::new(),
        OnDiskCorpus::new(PathBuf::from("./crashes")).unwrap(),
        &mut feedback,
        &mut objective,
    )
    .unwrap();
    let monitor = SimpleMonitor::new(|s| {
        println!("{}", s);
    });
    let mut mgr = SimpleEventManager::new(monitor);

    // Fuzzer, Executor
    let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);
    let mut binding = harness::fuzz_harness;
    let mut executor = InProcessExecutor::new(
        &mut binding,
        // tuple_list!(edges_observer, time_observer),
        tuple_list!(observer),
        &mut fuzzer,
        &mut state,
        &mut mgr,
    )
    .unwrap();

    if continue_on_errors {
        unsafe { harness::CONTINUE_ON_ERRORS = true };
    }

    if save_errors {
        unsafe { harness::SAVE_ERRORS = true };
    }

    println!("Preparing for corpus...\n");
    // Corpus
    if state.corpus().count() < 1 {
        if corpus_input.is_some() {
            let corpus_dirs = vec![PathBuf::from(corpus_input.unwrap())];
            println!("{:?}", corpus_dirs);
            state
                .load_initial_inputs_forced(&mut fuzzer, &mut executor, &mut mgr, &corpus_dirs)
                .unwrap_or_else(|err| {
                    panic!(
                        "Failed to load initial corpus at {:?}: {:?}",
                        &corpus_dirs, err
                    )
                });
            println!("Preparing for corpus OK!!\n");
        } else {
            let mut generator = RandBytesGenerator::new(16384);
            state
                .generate_initial_inputs(&mut fuzzer, &mut executor, &mut generator, &mut mgr, 32)
                .expect("Failed to generate the initial corpus");
        }
        println!("We imported {} inputs from disk.", state.corpus().count());
    }

    println!("Preparing for corpus OK!!!\n");
    if random_input {
        println!("We are using random input bytes");
        unsafe { harness::USE_RANDOM_INPUT = true };
    }

    // Mutator
    println!("Fuzzing Mutator...\n");
    let mutator = StdScheduledMutator::new(havoc_mutations());
    let mut stages = tuple_list!(StdMutationalStage::new(mutator));
    println!("Fuzzing Mutator OK...\n");
    // Fuzzing Loop
    println!("Fuzzing Looping...\n");
    if max_iters.is_some() {
        println!("Running the Fuzzer for {} iterations.", max_iters.unwrap());
        fuzzer
            .fuzz_loop_for(
                &mut stages,
                &mut executor,
                &mut state,
                &mut mgr,
                max_iters.unwrap(),
            )
            .expect("Fuzzer should not run into errors.");
    } else {
        println!("Running the Fuzzer for unlimited iterations.");
        fuzzer
            .fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)
            .expect("Error in the fuzzing loop");
    }

    println!("Fuzzing corpus_output\n");
    if corpus_output.is_some() {
        monitor::store_testcases(&mut state, corpus_output.unwrap());
    };


    println!("Fuzzing Looping OK...\n");
    let cover_points_output = "./tmp/cover_points.csv";
    println!("Storing cover points:{:?}\n", cover_points_output);
    harness::store_cover_points(cover_points_output.to_string());

    // Store cover points
    // println!("Storing cover points\n");
    // if cover_points_output.is_some() {
    //     store_cover_points(cover_points_output);
    // }
}
