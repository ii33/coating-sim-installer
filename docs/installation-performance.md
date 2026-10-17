# Native installation performance and progress

The compact GUI still starts with a path, Install, and collapsed details. One click uses CNB source, verified Huawei private Python/MinGit mirrors, TUNA packages, private CPython3.12.10 x64, and the existing DirectML-compatible package policy.

Python's complete downloaded archive must pass its pinned hash before component pipelining starts. Git preparation may then overlap Python extraction in a separate destination; source checkout waits for usable Git. File workers remain bounded to four, preserve all archive/path/hash/inventory checks, and join before staging cleanup or atomic publication. Directory structure is preflighted and prepared before file workers start. Runtime and venv transaction locks remain in force.

Only one pip process writes an environment at a time. The wheel-only resolver dry-run remains. pip installs the wheels without its serial bytecode step; the selected target Python then fully compiles bytecode with up to four worker processes while the venv lock is held. Cancellation uses the existing owned process-tree cleanup. No dependency, antivirus exemption, hash bypass, global configuration or user-environment mutation was added.

## Observed native Windows comparison

These are local measurements on the same machine, exact package set, fresh task-only destinations and verified input bytes. They are observations, not a performance guarantee for other machines or networks.

| Workload | Before | After |
| --- | ---: | ---: |
| Resolve + install + complete bytecode, 105 wheels / 495,359,183 bytes, offline | 84.87s | 48.81s |
| pip install stage | 82.29s including bytecode | 31.60s plus 14.67s parallel bytecode |
| CPython extraction / verification, identical 1322-file inventory, alternating fresh paths | 6.94–9.06s | 3.21–3.40s |
| Real mirror Python + Git bootstrap, original serial vs optimized pipeline | 14.33s | 6.03s |
| Isolated pipeline comparison, same optimized extractor: serial vs overlapped | 8.32s | 5.21s |

Both offline installs passed pip check and produced identical package/version inventories. NumPy2.5.4, Torch2.4.1+cpu, DirectML0.2.5.dev240914 and PySide6 imports passed. Large wheel unpacking still uses pip's single writer; it is not described as parallel package installation. The original profile found archive SHA hashing below0.03s, Git extraction below1s, venv/ensurepip around3–4s; these are not the dominant dependency-install cost.

## Truthful live progress

Install automatically reveals an activity bar, current stage, completed-stage count, monotonic elapsed time, component-labelled streaming logs, copy/save controls and Cancel. No overall linear percentage is invented. Known HTTP lengths and pip25 raw byte counters provide downloaded/total bytes; unknown totals remain unknown. Speeds use monotonic deltas and smoothing. ETA appears only after stable samples with a known length. Per-component counters reset on source/attempt changes or byte resets; aggregation sums independent active speeds, never duplicate observations. Stalled/finished/failed/cancelled transfers clear old speeds. pip versions without raw progress support retain logs and stage activity.

Reader queues, GUI queues and visible history are bounded; GUI log writes are batched and redacted. Complete recovery diagnostics and saved logs remain available. Stage durations distinguish runtime download/hash/preflight/extraction, venv creation, wheel resolution, pip install, parallel bytecode and final verification.
