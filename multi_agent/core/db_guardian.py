#!/usr/bin/env python3
"""SQLite 数据库健康守护与备份方案。

目标：防止 `llm_predictions.db` 再次损坏/丢失历史数据。
职责：
1. 每次写入后主动执行 `PRAGMA integrity_check` 与 `VACUUM INTO` 备份。
2. 保留滚动快照：按小时/天生成 `.db.<timestamp>.snap`，最多保留 N 份。
3. 损坏检测：启动时检测是否为 malformed，若损坏则从最新健康快照恢复。
4. 写入日志：记录每个备份/恢复事件，便于审计。
"""

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


DEFAULT_DB_PATH = "multi_agent/data/llm_predictions.db"
DEFAULT_SNAPSHOT_DIR = "multi_agent/data/db_snapshots"
DEFAULT_RETENTION_HOURS = 72
DEFAULT_MAX_SNAPSHOTS = 24


def log_event(path: Path, event: str, detail: str = ""):
    line = json.dumps({"time": datetime.now().isoformat(), "event": event, "detail": detail}, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def integrity_check(db_path: str) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        res = cur.fetchone()
        conn.close()
        return res and res[0] == "ok"
    except Exception as e:
        print(f"integrity_check failed: {e}")
        return False


def snapshot_db(db_path: str, snapshot_dir: str, retention_hours: int, max_snapshots: int) -> Optional[str]:
    """生成健康快照，清理过期快照。"""
    db = Path(db_path)
    if not db.exists():
        return None
    if not integrity_check(db_path):
        raise RuntimeError(f"Database {db_path} is malformed; refuse to snapshot.")

    out_dir = Path(snapshot_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = out_dir / f"{db.name}.{timestamp}.snap"

    # VACUUM INTO 生成一致的热备份
    conn = sqlite3.connect(db_path)
    conn.execute(f"VACUUM INTO '{snap_path.as_posix()}'")
    conn.close()

    # 清理过期快照
    cutoff = datetime.now() - timedelta(hours=retention_hours)
    snaps = sorted(out_dir.glob(f"{db.name}.*.snap"), key=lambda p: p.stat().st_mtime)
    for s in snaps:
        if datetime.fromtimestamp(s.stat().st_mtime) < cutoff or len(snaps) > max_snapshots:
            s.unlink(missing_ok=True)
            snaps.remove(s)

    return str(snap_path)


def restore_latest_snapshot(db_path: str, snapshot_dir: str) -> Optional[str]:
    """从最新快照恢复数据库。"""
    db = Path(db_path)
    out_dir = Path(snapshot_dir)
    snaps = sorted(out_dir.glob(f"{db.name}.*.snap"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not snaps:
        return None
    latest = snaps[0]
    backup_bad = Path(str(db) + f".corrupt_{datetime.now().strftime('%Y%m%d%H%M%S')}")
    if db.exists():
        shutil.move(db, backup_bad)
    shutil.copy2(latest, db)
    return str(latest)


def ensure_db_healthy(db_path: str = DEFAULT_DB_PATH, snapshot_dir: str = DEFAULT_SNAPSHOT_DIR) -> dict:
    log_path = Path(snapshot_dir) / "db_guardian.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    result = {"db_path": db_path, "healthy": False, "action": "none", "snapshot": None}

    if Path(db_path).exists() and integrity_check(db_path):
        result["healthy"] = True
        try:
            snap = snapshot_db(db_path, snapshot_dir, DEFAULT_RETENTION_HOURS, DEFAULT_MAX_SNAPSHOTS)
            result["snapshot"] = snap
            result["action"] = "snapshot"
            log_event(log_path, "snapshot", f"snap={snap}")
        except Exception as e:
            result["action"] = "snapshot_failed"
            log_event(log_path, "snapshot_failed", str(e))
    else:
        result["action"] = "restore"
        restored_from = restore_latest_snapshot(db_path, snapshot_dir)
        result["snapshot"] = restored_from
        if restored_from:
            result["healthy"] = integrity_check(db_path)
            log_event(log_path, "restore", f"from={restored_from}, healthy={result['healthy']}")
        else:
            log_event(log_path, "no_snapshot_available", "Database corrupt and no snapshot found.")

    return result


def main():
    result = ensure_db_healthy()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["healthy"] and not result["snapshot"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
