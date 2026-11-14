#!/usr/bin/env python3
"""Report exported payload sizes; these are not App Store thinned install sizes."""
import argparse
import pathlib
import zipfile


def report(path):
    with zipfile.ZipFile(path) as archive:
        files = [entry for entry in archive.infolist()
                 if entry.filename.startswith('Payload/') and not entry.is_dir()]
    if not files:
        raise ValueError('IPA contains no Payload files')
    lines = ['Exported IPA size report (before App Store processing/thinning)',
             f'IPA bytes: {path.stat().st_size}',
             f'Uncompressed payload bytes: {sum(entry.file_size for entry in files)}',
             f'Payload file count: {len(files)}', 'Largest payload files:']
    lines.extend(f'{entry.file_size:12d}  {entry.filename}'
                 for entry in sorted(files, key=lambda item: item.file_size, reverse=True)[:20])
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ipa', type=pathlib.Path)
    args = parser.parse_args()
    print(report(args.ipa))
