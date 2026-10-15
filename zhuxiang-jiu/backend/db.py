# -*- coding: utf-8 -*-
"""85号·持久化层连接入口(Day1)

- 连接串从环境变量 DRAMA85_DB_URL 读取(落 backend/.env, 已 gitignore);
- 严禁将连接串(含密码)写入任何日志;
- 本地默认指向 drama85-mysql 容器(127.0.0.1:3306, 库 drama85)。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_URL = "mysql+pymysql://root:drama85_root_2026@127.0.0.1:3306/drama85?charset=utf8mb4"


def _load_env_var(key: str) -> str | None:
    """从同目录 .env 读取 key(极简解析, 不引额外依赖); 环境变量优先。"""
    if os.environ.get(key):
        return os.environ[key]
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line[len(key) + 1:].strip()
    except OSError:
        pass
    return None


DB_URL = _load_env_var("DRAMA85_DB_URL") or DEFAULT_DB_URL

# pool_pre_ping: 容器重启/断连后自动恢复; echo 永远关闭(防 SQL+凭据泄日志)
engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=3600, echo=False)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def get_session() -> Session:
    """事务上下文: with get_session() as s: ...  异常自动回滚。"""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_schema(sql_file: str | None = None) -> int:
    """执行 drama85_schema.sql 建库建表(幂等, IF NOT EXISTS)。

    返回执行的语句条数。sql_file 缺省取本文件同目录下的 drama85_schema.sql。
    """
    path = sql_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "drama85_schema.sql")
    with open(path, "r", encoding="utf-8") as f:
        sql_text = f.read()
    # 去掉行注释与空行后按分号切分(MySQL 8 下 CREATE ... ; 单语句安全)
    stmts = []
    buf: list[str] = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).rstrip(";").strip()
            if stmt:
                stmts.append(stmt)
            buf = []
    with engine.connect() as conn:
        for i, stmt in enumerate(stmts):
            conn.execute(text(stmt))
        conn.commit()
    return len(stmts)


if __name__ == "__main__":
    n = init_schema()
    with engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text("SHOW TABLES"))]
    print("schema_ok stmts=%d tables=%s" % (n, tables))
