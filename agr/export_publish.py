"""Restore the previous scene/package/report when a final save operation fails."""
import json
import os
from pathlib import Path


def publish_export(scene, manifest, output, report, *, overwrite, stage_package):
    """Stage first, then replace all three outputs with rollback on save errors.

    Backups stay in the owned conversion work directory until normal successful
    job cleanup. This is not crash/power-loss recovery or a concurrent-reader lock.
    """
    scene,output=Path(scene),Path(output)
    work=scene.parent
    package=output.with_suffix('.agrscene')
    report_path=output.with_suffix('.agr-report.json')
    destinations=(output,package,report_path)
    for destination in destinations:
        if destination.is_symlink() or getattr(destination,'is_junction',lambda:False)():
            raise ValueError(f'Linked export output is not supported: {destination}')
        if destination.exists():
            if not overwrite:
                raise ValueError('Output or companion package already exists; choose a new name or enable overwrite.')
            if destination==package:
                marker=package/'format.json'
                if (not package.is_dir() or not marker.is_file()
                        or json.loads(marker.read_text(encoding='utf-8'))!={'format':'deadlock-agr-scene','version':1}):
                    raise ValueError(f'Scene package is not owned by RenderLock: {package}')
            elif not destination.is_file():
                raise ValueError(f'Expected an output file: {destination}')
    staged_package=work/'publish-package'
    staged_report=work/'publish-report.json'
    if staged_package.exists() or staged_report.exists():
        raise ValueError('This export work directory already contains a save attempt; preserve it for diagnostics.')
    stage_package(manifest,staged_package)
    staged_report.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    backup=work/'publish-backup'
    backup.mkdir()
    previous=[]
    installed=[]
    try:
        for destination in destinations:
            if destination.exists():
                if not overwrite:
                    raise FileExistsError(f'Output appeared while preparing the save: {destination}')
                saved=backup/destination.name
                destination.rename(saved)
                previous.append((saved,destination))
        for source,destination in ((scene,output),(staged_package,package),(staged_report,report_path)):
            os.replace(source,destination)
            installed.append((source,destination))
    except BaseException as error:
        problems=[]
        # Move failed-job outputs back into the retained work directory; do not
        # delete them or overwrite an old file whose restoration also failed.
        for source,destination in reversed(installed):
            try:destination.rename(source)
            except OSError as rollback_error:problems.append(str(rollback_error))
        for saved,destination in reversed(previous):
            try:
                if destination.exists():raise FileExistsError(f'Cannot restore occupied output: {destination}')
                saved.rename(destination)
            except OSError as rollback_error:problems.append(str(rollback_error))
        if problems:
            raise RuntimeError(f'Export save failed ({error}) and restoration could not finish. Previous files are retained at {backup}. '
                               +' | '.join(problems)) from error
        raise
