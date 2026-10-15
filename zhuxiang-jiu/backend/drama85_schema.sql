-- -*- sql -*-
-- 85号·短剧榜单持久化层建库建表(Drama85 Day1)
-- 依据: docs/短剧榜单采集持久化落地方案.md 第二节(修正版 DDL)
-- 执行: mysql -h127.0.0.1 -uroot -p < drama85_schema.sql  或 db.py 初始化
-- 幂等: 库/表均 IF NOT EXISTS, 可重复执行

CREATE DATABASE IF NOT EXISTS drama85
  DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE drama85;

-- 1. 短剧主表(核心修正: dedup_key 生成列 = itemId 优先 / 剧名|主平台 兜底,
--    与 worker collect 归一逻辑 key = itemId or title 对齐, 同剧双入口不插重)
CREATE TABLE IF NOT EXISTS drama (
    drama_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '短剧ID',
    item_id         VARCHAR(64)     DEFAULT NULL COMMENT '平台内视频ID(如 sv_ 前缀); 同剧双入口归一根键',
    drama_name      VARCHAR(255)    NOT NULL COMMENT '剧名(契约 title)',
    main_platform   VARCHAR(32)     DEFAULT NULL COMMENT '主平台/采集入口: baidu/wukong/xigua/hongguo/xinbang/dataeye/kuaishou/maoyan',
    platforms       VARCHAR(255)    DEFAULT NULL COMMENT '多平台分发列表(契约 platforms, 逗号分隔, matcher 跨源合并时更新)',
    episodes        INT             DEFAULT NULL COMMENT '集数(契约 episodes; videocollect 无集号为0如实, 偶带合集总集数如岁月如金1983=983)',
    duration_sec    INT             DEFAULT NULL COMMENT '单集时长秒(契约 durationSec)',
    genre_tags      JSON            DEFAULT NULL COMMENT '题材标签(契约 tags)',
    is_ai_drama     TINYINT(1)      NOT NULL DEFAULT 0 COMMENT 'AI漫剧/AI短剧: 0否1是(cleaner 依剧名/标签识别)',
    publisher       VARCHAR(255)    DEFAULT NULL COMMENT '发行方',
    producer        VARCHAR(255)    DEFAULT NULL COMMENT '制作方',
    copyright_owner VARCHAR(255)    DEFAULT NULL COMMENT '版权方',
    subtitle_text   TEXT            DEFAULT NULL COMMENT '简介摘要(契约 subtitleText 截断存储)',
    first_seen_at   DATETIME        DEFAULT NULL COMMENT '首次入库/上榜时间',
    last_seen_at    DATETIME        DEFAULT NULL COMMENT '最近在池/上榜时间',
    status          VARCHAR(32)     NOT NULL DEFAULT 'active' COMMENT 'active/offline/new',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    dedup_key       VARCHAR(320)    GENERATED ALWAYS AS
                        (COALESCE(NULLIF(item_id, ''), CONCAT(drama_name, '|', main_platform))) STORED
                        COMMENT '归一键: itemId 优先, 剧名|主平台 兜底',
    PRIMARY KEY (drama_id),
    UNIQUE KEY uk_dedup (dedup_key),
    KEY idx_platform (main_platform),
    KEY idx_status (status),
    KEY idx_last_seen (last_seen_at),
    KEY idx_drama_name (drama_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='短剧主表';

-- 2. 榜单/池快照表(share 池日快照 + 外部榜单期快照共用)
CREATE TABLE IF NOT EXISTS drama_rank_snapshot (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source          VARCHAR(32)     NOT NULL COMMENT '快照来源: baidu/wukong/xigua(share池日快照) / xinbang/dataeye/kuaishou/maoyan(榜单) / huitun(通道①)',
    rank_type       VARCHAR(32)     NOT NULL COMMENT 'share_pool(自有池) / hot / rising / new / weekly / monthly',
    rank_no         INT             DEFAULT NULL COMMENT '排名(榜单类必填; share_pool 池内序可为 NULL)',
    drama_id        BIGINT UNSIGNED DEFAULT NULL COMMENT '关联主表(匹配失败时 NULL, 待 matcher 补链)',
    drama_name      VARCHAR(255)    NOT NULL COMMENT '剧名冗余(原始口径保留)',
    heat_value      BIGINT          DEFAULT NULL COMMENT '热度值(cleaner 换算后整数; 缺失为 NULL 不造数)',
    heat_unit       VARCHAR(16)     DEFAULT NULL COMMENT '原始单位: wan/yi/raw(换算前口径)',
    play_delta      BIGINT          DEFAULT NULL COMMENT '播放增量(部分榜单可得)',
    period_start    DATE            DEFAULT NULL COMMENT '统计周期起',
    period_end      DATE            DEFAULT NULL COMMENT '统计周期止',
    snapshot_time   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_url         VARCHAR(512)    DEFAULT NULL COMMENT '原始页面/端点链接',
    raw_json        JSON            DEFAULT NULL COMMENT '原始契约/页面数据(含 metrics 区间值原样)',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_snap (source, rank_type, drama_name, period_start, period_end, snapshot_time),
    KEY idx_source_type (source, rank_type, snapshot_time),
    KEY idx_period (period_start, period_end),
    KEY idx_drama (drama_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='榜单/池快照表';

-- 3. 榜单明细表(跨源对齐后的规范行, 一期可与快照表合并使用, 二期启用)
CREATE TABLE IF NOT EXISTS drama_rank_item (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    snapshot_id     BIGINT UNSIGNED NOT NULL COMMENT '关联快照',
    drama_id        BIGINT UNSIGNED NOT NULL COMMENT '关联主表(matcher 合并后)',
    rank_no         INT             NOT NULL COMMENT '当期排名',
    heat_value      BIGINT          DEFAULT NULL,
    play_delta      BIGINT          DEFAULT NULL,
    period_start    DATE            DEFAULT NULL,
    period_end      DATE            DEFAULT NULL,
    source          VARCHAR(32)     NOT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_snapshot (snapshot_id),
    KEY idx_drama (drama_id),
    KEY idx_source_period (source, period_start, period_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='榜单明细表';

-- 4. 采集任务表
CREATE TABLE IF NOT EXISTS crawl_job (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source          VARCHAR(32)     NOT NULL COMMENT '任务源(同 snapshot.source)',
    channel         TINYINT         NOT NULL DEFAULT 2 COMMENT '通道: 1=第三方API / 2=自建公开页 / 3=官方开放',
    url             VARCHAR(512)    NOT NULL COMMENT '采集地址或端点(8510/8511 /dramas 也是合法值)',
    method          VARCHAR(16)     DEFAULT 'html' COMMENT 'html/api/contract/manual',
    cron_expr       VARCHAR(32)     DEFAULT NULL COMMENT '调度表达式(APScheduler 对照)',
    status          VARCHAR(16)     DEFAULT 'pending' COMMENT 'success/failed/skipped/pending',
    last_run_at     DATETIME        DEFAULT NULL,
    next_run_at     DATETIME        DEFAULT NULL,
    error_msg       TEXT            DEFAULT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_job (source, url),
    KEY idx_source_status (source, status),
    KEY idx_next_run (next_run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='采集任务表';

-- 5.(预留, 二期)向量知识表 drama_knowledge: 剧情片段/爽点模式/评论摘要/向量ID
