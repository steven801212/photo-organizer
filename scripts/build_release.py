"""Build an allowlisted NAS source archive; never include credentials or photos."""
import hashlib
import json
from pathlib import Path
import re
import tomllib
import zipfile


def main():
    root=Path(__file__).resolve().parents[1]
    version=tomllib.loads((root/'pyproject.toml').read_text('utf-8'))['project']['version']
    declared=re.search(r'__version__ = "([^"]+)"',(root/'src/photo_organizer/__init__.py').read_text('utf-8'))[1]
    assert version==declared and re.fullmatch(r'\d+\.\d+\.\d+', version)
    assert f'image: photo-organizer:{version}' in (root/'docker-compose.yml').read_text('utf-8')
    assert b'\r' not in (root/'docker-entrypoint.sh').read_bytes()
    fixed=['.dockerignore','.env.example','Dockerfile','docker-entrypoint.sh','docker-compose.yml',
           'pyproject.toml','README.md','RELEASE-NOTES.md','RELEASE-REVIEW.md','SYNOLOGY.md',
           'ARCHITECTURE.md','CHANGELOG.md','artifacts/release-validation.txt','scripts/build_release.py']
    files=[root/name for name in fixed]
    for folder in ('src/photo_organizer','tests'):
        files.extend(p for p in (root/folder).rglob('*') if p.is_file() and
                     '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'})
    manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    destination=root/f'photo-organizer-v{version}-synology.zip'
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(files):archive.write(p,p.relative_to(root).as_posix())
        archive.writestr('RELEASE-MANIFEST.json',json.dumps({'version':version,'sha256':manifest},indent=2))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name,digest in manifest.items():assert hashlib.sha256(archive.read(name)).hexdigest()==digest
        assert '.env' not in archive.namelist()
        assert 'src/photo_organizer/storage_lock.py' in archive.namelist()
        assert 'src/photo_organizer/web/vendor/leaflet/LICENSE' in archive.namelist()
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(f'{digest}  {destination.name}\n',encoding='ascii')
    print(json.dumps({'archive':str(destination),'files':len(files),'bytes':destination.stat().st_size,'sha256':digest},indent=2))


if __name__=='__main__':main()
