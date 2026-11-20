# Auto-start when a worker process imports this module while resolving
# worker_extension_cls (args.py injects the class path as a literal for the
# same reason: importing this module starts the policy in the importing
# process). The engine-core process instead imports lazily and starts from
# InstrumentedScheduler._bench_init, so serving without an active benchmark
# never runs the policy. No-op unless DYN_FPM_GC_POLICY is set.
start_gc_policy()
