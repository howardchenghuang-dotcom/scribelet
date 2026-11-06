"""Online SQLite snapshot, including original audio; session cookies are not retained."""
import argparse
from contextlib import closing
import datetime
import hashlib
from pathlib import Path
import sqlite3

def backup(source, destination):
    source=Path(source).resolve()
    destination=Path(destination).resolve()
    if not source.is_file() or destination.exists():
        raise ValueError('数据库不存在或目标文件已存在，未进行覆盖。')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer)
        writer.execute('DELETE FROM sessions')
        writer.execute('DELETE FROM attempts')
        writer.commit()
        if writer.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('备份完整性检查失败')
    with destination.open('rb') as file:
        digest=hashlib.file_digest(file,'sha256').hexdigest()
    destination.with_suffix('.sha256').write_text(digest+'  '+destination.name+'\n',encoding='utf-8')
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('destination')
    args=parser.parse_args()
    print(backup(args.source,args.destination))
