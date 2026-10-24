# Isolated private compute roles (implementation checkpoint)

This is an **opt-in source CLI transaction** included with alpha.12 source, not yet the default interactive/GUI installer path or an entry exposed by the frozen EXE. Windows x64 end-to-end acceptance is still required. The legacy GUI remains a one-venv path; do not describe this checkpoint as completing the default-installation requirement.

## Layout and ownership

The source checkout must implement the schema-1 `coat_sim.compute_runtime_manifest` consumer. First prepare the pinned, installer-owned `.runtime/python` CPython with `runtime-plan` / `runtime-install`. The role commands never discover, select, or reuse external Python.

| Environment | Location | Role / requirements |
| --- | --- | --- |
| app | `.venv` | Full desktop dependencies, CPU deposition alias |
| deposition_directml | `.runtime/envs/deposition-directml` | CPU scientific roots + reviewed DirectML pin |
| deposition_cuda | `.runtime/envs/deposition-cuda` | CPU scientific roots + hardware-adapted CUDA12 or CUDA13 profile |
| appearance | `.runtime/envs/appearance` | CPU scientific roots + Mitsuba 3.9.0 / Dr.Jit 1.4.0 |

DirectML is included on supported Windows even when a CUDA profile is selected. The CUDA environment is omitted when no reviewed default-device/driver combination is proven, or when explicitly selecting CPU. CUDA and DirectML do not modify each other's package set. No driver, system Toolkit, PATH, registry, or unrelated environment is changed. Film GPU-forward dispatch is not added.

## Review and execution

Run the standalone installer with:

```
python -m tools.dependency_manager runtime-roles-plan --project C:\Coating\app --runtime-cuda auto --json
python -m tools.dependency_manager runtime-roles-install --project C:\Coating\app --runtime-cuda auto --plan-id REVIEWED_PLAN_ID --yes --json
```

The Python running this source CLI front-end is not the application bootstrap Python. The current frozen EXE entry does not forward these CLI commands; run them from the installer source checkout. Role creation uses only the checked private base, never `--python`, `--venv`, or `--runtime-dir` overrides. `--runtime-cuda cpu` means no CUDA environment; it does not remove DirectML. Development tooling is included only with explicit `--profile development`.

Planning is read-only: it records requirements-file hashes, fixed environment scopes, package/interpreter/config snapshots, GPU evidence, and the prior binding-file hash. Execution requires the current exact plan, serializes the operation, revalidates source scope and ownership, and uses the existing binary-only resolver/install/verification transaction independently per environment. Automatic CUDA selection is only an installation candidate, not a readiness claim.

## Manifest publication and failures

Only after all requested environments verify does the transaction publish complete bytes to `.runtime/compute-runtimes.json`. New publication is create-only. For replacement it first moves the prior file into a unique owned recovery directory, verifies what actually moved, then publishes create-only again. A concurrent unrelated writer is restored or retained with the previous copy rather than overwritten. Consumers refuse the operation lock during this brief replacement gap, so a missing file cannot accidentally enable a legacy route. Exact schema keys are `schema_version`, `platform`, `architecture`, `base`, `environments`, and `roles`. The base contains the fixed relative root/interpreter, Python version, and pinned owned-file inventory digest. Each environment contains its fixed relative root/interpreter, backend, interpreter/config hashes, full normalized installed-distribution name/version inventory, and canonical inventory digest. The main-repository contract documents selection and validation in detail.

On cancellation, scope change, or component failure, existing and partially prepared environments are preserved, remaining components stop, and no new binding file is published. An old binding file is preserved too, but it is not refreshed or counted as current evidence: the strict consumer must reject a changed interpreter, venv config, or distribution inventory. A failed attempt may therefore require repair before managed workers can launch.

An installed package inventory is **not** complete wheel-byte authenticity, native-DLL closure, CUDA/DirectML readiness, renderer qualification, or a clean zero-Python-machine test. Independent runtime/device/numerical probes remain mandatory at their existing execution boundaries. The current Linux checks cover orchestration, failure preservation, contract parity, and static validation only.

## Interrupted transaction recovery

The operation writes `.runtime/compute-runtime-install.lock` with its PID, but a PID alone is not evidence that a process is safe to terminate or that a stale lock can be stolen. Normal completion removes only the exact lock inode created by that operation. Process death can leave a lock and an owned `.compute-runtimes-recovery-*` directory. Neither is removed automatically.

If a managed worker reports preparation/recovery in progress, first confirm the related installer and package processes have actually stopped. Preserve all runtime environments and any recovery copies. Only then remove that stale operation-lock file, inspect any retained recovery-copy diagnostic, and rerun `runtime-roles-plan` followed by a newly confirmed `runtime-roles-install`. A previously reviewed plan is not reused after recovery. A conflicting current manifest is preserved and requires inspection, not an automatic overwrite or cleanup.

## Reproducible offline checks

`pytest -q tests/test_runtime_roles.py` exercises fixed scopes, coexistence, cancellation, failures, early interpreter authentication, GPU-root conflict parity, and both late manifest-race windows. Set `COAT_RUNTIME_CONSUMER_ROOT` to a current main-repository checkout and additionally run `tests/test_runtime_manifest_consumer_contract.py` to verify real-file producer/consumer parity and the app-repair-then-DirectML-failure stale-binding case. Those fixtures are inert bytes and never prove a working Windows runtime or GPU.
