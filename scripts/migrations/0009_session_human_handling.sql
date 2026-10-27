-- 0009：会话状态机补 human_handling（人工接管中）到 sessions.status 的 CHECK 约束
--
-- 为什么需要：人工接管引入会话状态 active → handover（排队）→ human_handling（坐席接入）
-- → active/closed（释放）。0007 之前的建表脚本只把 status 约束到 ('active','closed','handover')，
-- 缺 'human_handling'，导致坐席 claim 写 human_handling 时触发 sessions_status_check 违反、
-- 接口 500。SQLAlchemy 模型未声明该 CHECK（仅 init_db.sql 有），故存量库需本迁移补齐。
--
-- 幂等：先 DROP IF EXISTS 再 ADD，可在"已是新结构"的库上重放；ADD 会校验既有行，
-- 现存会话取值均落在新集合内，不会因历史数据失败。

ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_status_check;
ALTER TABLE sessions
    ADD CONSTRAINT sessions_status_check
    CHECK (status IN ('active','closed','handover','human_handling'));
