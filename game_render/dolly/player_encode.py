"""Isolated Players conversion/encoding with bounded resource policies."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

MODES = ('Background', 'Balanced', 'Fast')


def resource_policy(mode):
    if mode not in MODES:
        raise ValueError('Choose Background, Balanced or Fast Players processing.')
    threads = {'Background': 1, 'Balanced': 2, 'Fast': 4}[mode]
    priority = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0) if mode == 'Background' else 0
    return threads, priority


class ProcessJob:
    """Windows kill-on-close job; worker waits for admission before spawning FFmpeg."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64),
                        ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                        ('active', w.DWORD), ('affinity', c.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', c.c_uint64 * 6),
                        ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                        ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
        self.api = c.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.CloseHandle.restype = w.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not (self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits))
                and self.api.AssignProcessToJobObject(self.handle, int(process._handle))):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _write_json(path, value):
    temporary = Path(path).with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temporary, path)


def _write_progress(folder, count):
    # Windows readers can temporarily deny replacement while their file is open.
    # Progress is advisory; request/result publication still uses strict writes.
    try:
        _write_json(folder / 'progress.json', dict(frames=count))
    except OSError:
        pass


def encode_in_worker(ffmpeg, bundle, output, *, fps=60, resource_mode='Background',
                     cancel=None, progress=lambda count: None):
    """Keep GUI Python/GIL independent; publish only a complete, uncancelled MOV."""
    from .runtime import is_frozen
    _, priority = resource_policy(resource_mode)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staged = output.with_name(output.stem + '.' + token + '.worker.mov')
    partial = output.with_name(output.stem + '.' + token + '.partial.mov')
    process = job = None
    with tempfile.TemporaryDirectory(prefix='dolly-players-') as folder:
        folder = Path(folder)
        request = folder/'request.json'
        _write_json(request, dict(ffmpeg=str(Path(ffmpeg).resolve()), bundle=str(Path(bundle).resolve()),
                   output=str(staged), partial=str(partial), fps=fps, resource_mode=resource_mode))
        command = ([sys.executable, '--player-encode-worker', str(request)] if is_frozen()
                   else [sys.executable, '-m', 'dolly.player_encode', str(request)])
        environment = dict(os.environ)
        environment.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                           NUMEXPR_NUM_THREADS='1', PYINSTALLER_RESET_ENVIRONMENT='1')
        try:
            with (folder/'worker.log').open('wb') as errors:
                process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                    stdout=errors, stderr=errors, env=environment,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | priority)
                job = ProcessJob(process)
                (folder/'go').touch()
                cancelled_at = None
                last_count = -1
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        if cancelled_at is None:
                            cancelled_at = time.monotonic()
                            (folder/'cancel').touch()
                        elif time.monotonic() - cancelled_at > 5:
                            # Windows job termination includes the encoder child.
                            job.close()
                            if process.poll() is None: process.kill()
                            break
                    try:
                        count = json.loads((folder/'progress.json').read_text())['frames']
                        if count != last_count:
                            progress(count)
                            last_count = count
                    except (OSError, ValueError, KeyError):
                        pass
                    time.sleep(.1)
                process.wait(timeout=10)
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Players processing cancelled; raw capture retained.')
            try:
                result = json.loads((folder/'result.json').read_text())
            except (OSError, ValueError) as exc:
                raise RuntimeError(f'Players worker exited without a result (exit {process.returncode}).') from exc
            if process.returncode or not result.get('ok'):
                raise RuntimeError(result.get('error', 'Players processing failed.'))
            if not staged.is_file() or not staged.stat().st_size:
                raise RuntimeError('Players worker produced no MOV.')
            os.replace(staged, output)
            return output
        finally:
            if job: job.close()
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for temporary in (partial, staged):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # An owned leftover must not hide the processing result.


def worker_main(request):
    """Private entry point; never initializes the editor or accesses the game."""
    request = Path(request)
    folder = request.parent
    try:
        deadline = time.monotonic() + 30
        while not (folder/'go').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('Players worker admission timed out.')
            time.sleep(.05)
        options = json.loads(request.read_text(encoding='utf-8'))
        from .player_layer import encode_bundle
        encode_bundle(Path(options['ffmpeg']), Path(options['bundle']), Path(options['output']),
            fps=options['fps'], resource_mode=options['resource_mode'],
            partial_path=Path(options['partial']), cancelled=lambda: (folder/'cancel').exists(),
            progress=lambda count: _write_progress(folder, count))
        _write_json(folder/'result.json', dict(ok=True))
        return 0
    except Exception as exc:
        _write_json(folder/'result.json', dict(ok=False, error=str(exc)))
        return 1


if __name__ == '__main__':
    raise SystemExit(worker_main(sys.argv[1]))
