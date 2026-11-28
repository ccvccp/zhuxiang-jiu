import copy
import re
from urllib.parse import urlparse

from lifecycle import ensure_lifecycle, set_lifecycle


def _append_unique(items, value):
    if value not in items:
        items.append(value)


def _github_url(value):
    parsed = urlparse(value) if isinstance(value, str) else None
    if not parsed or parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith("/ccvccp/zhuxiang-jiu/"):
        raise ValueError(f"GitHub URL 必须属于 ccvccp/zhuxiang-jiu：{value}")
    return value


def _positive_number(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} 必须为正整数")
    return value


def _commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("提交号必须为 40 位小写十六进制")
    return value


def _upsert(records, record, key):
    for index, existing in enumerate(records):
        if existing[key] == record[key]:
            if existing != record:
                records[index] = copy.deepcopy(record)
            return
    records.append(copy.deepcopy(record))


def apply_development_update(manifest, chapters, update):
    if not isinstance(update, dict):
        raise ValueError("开发同步输入必须为对象")
    synced = ensure_lifecycle(manifest)
    updated = copy.deepcopy(chapters)
    if not update:
        return synced, updated, []
    milestones = []
    development = synced["development"]
    for issue in update.get("issues", []):
        number = _positive_number(issue.get("number"), "Issue 编号")
        if not isinstance(issue.get("title"), str) or not issue["title"].strip():
            raise ValueError("Issue title 不能为空")
        record = {"number": number, "title": issue["title"], "url": _github_url(issue.get("url"))}
        _upsert(development["issues"], record, "number")
        _append_unique(updated["planning"]["content"]["tasks"], f"Issue #{number}：{record['url']}")
        _append_unique(milestones, "development_tasks")
    for branch in update.get("branches", []):
        name = branch.get("name")
        if not isinstance(name, str) or not name.startswith("agent/"):
            raise ValueError("开发分支必须以 agent/ 开头")
        record = {"name": name, "commit": _commit(branch.get("commit"))}
        _upsert(development["branches"], record, "name")
        _append_unique(development["commits"], record["commit"])
    tested = update.get("code_tested")
    test_note = ""
    if tested is not None:
        commit = _commit(tested.get("commit"))
        if not isinstance(tested.get("command"), str) or not tested["command"].strip():
            raise ValueError("测试 command 不能为空")
        if type(tested.get("exit_code")) is not int:
            raise ValueError("测试 exit_code 必须为整数")
        if not isinstance(tested.get("output_summary"), str) or not tested["output_summary"].strip():
            raise ValueError("测试 output_summary 不能为空")
        _append_unique(development["commits"], commit)
        test_note = f"开发测试：{tested['command']}；exit_code={tested['exit_code']}；{tested['output_summary']}"
        _append_unique(updated["acceptance"]["content"]["notes"], test_note)
        if tested["exit_code"] == 0:
            _append_unique(milestones, "code_tested")
    for pr in update.get("pull_requests", []):
        number = _positive_number(pr.get("number"), "PR 编号")
        if pr.get("state") not in {"open", "closed"} or type(pr.get("merged")) is not bool:
            raise ValueError("PR state 或 merged 无效")
        if pr.get("review") not in {"pending", "approved", "changes_requested"}:
            raise ValueError("PR review 无效")
        if pr.get("ci") not in {"pending", "passed", "failed"}:
            raise ValueError("PR ci 无效")
        record = {"number": number, "url": _github_url(pr.get("url")), "state": pr["state"], "merged": pr["merged"], "review": pr["review"], "ci": pr["ci"]}
        existing = next((item for item in development["pull_requests"] if item["number"] == number), None)
        if existing is not None and existing["merged"]:
            record["state"] = "closed"
            record["merged"] = True
        _upsert(development["pull_requests"], record, "number")
        note = f"PR #{number}：state={record['state']}，review={record['review']}，ci={record['ci']}；{record['url']}"
        _append_unique(updated["acceptance"]["content"]["notes"], note)
        if record["state"] == "open" and record["review"] == "approved" and record["ci"] == "passed" and not record["merged"]:
            _append_unique(milestones, "pr_reviewed")
    merge = update.get("merge")
    if merge is not None:
        number = _positive_number(merge.get("pull_request"), "合并 PR 编号")
        pr_record = next((item for item in development["pull_requests"] if item["number"] == number), None)
        if pr_record is None:
            raise ValueError("合并记录必须对应已同步 PR")
        if merge.get("authorized") is not True:
            raise ValueError("缺少当次运行的合并授权")
        commit = _commit(merge.get("commit"))
        development["merge_commit"] = commit
        _append_unique(development["commits"], commit)
        pr_record["state"] = "closed"
        pr_record["merged"] = True
        synced["authorization"]["merge"] = {"run_id": synced["run_id"], "scope": f"PR #{number}", "confirmed": True}
        _append_unique(updated["acceptance"]["content"]["notes"], f"代码合并提交：{commit}；不代表生产部署或验收通过")
        _append_unique(milestones, "merged")

    # 最后统一计算状态：本批失败优先，显式合并其次，后期阶段不得被旧输入倒退。
    current = synced["lifecycle"]
    if tested is not None and tested["exit_code"] != 0:
        set_lifecycle(synced, "development", "failed", blocker=test_note)
    elif merge is not None:
        set_lifecycle(synced, "awaiting_deployment", "awaiting_confirmation", awaiting="deployment")
    elif current["stage"] in {"awaiting_deployment", "deployment", "acceptance", "delivered"}:
        pass
    elif current["status"] in {"failed", "blocked"} and tested is None and not update.get("pull_requests"):
        pass
    else:
        open_prs = [item for item in development["pull_requests"] if item["state"] == "open" and not item["merged"]]
        blocked = [item for item in open_prs if item["ci"] == "failed" or item["review"] == "changes_requested"]
        ready = [item for item in open_prs if item["ci"] == "passed" and item["review"] == "approved"]
        if blocked:
            numbers = "、".join(f"PR #{item['number']}" for item in blocked)
            set_lifecycle(synced, "review", "blocked", blocker=f"{numbers} 的 CI 失败或审查要求修改")
        elif ready:
            set_lifecycle(synced, "awaiting_merge", "awaiting_confirmation", awaiting="merge")
        elif tested is not None or open_prs:
            set_lifecycle(synced, "review", "in_progress")
        elif current["stage"] == "awaiting_merge":
            set_lifecycle(synced, "development", "blocked", blocker="PR 已关闭或不再满足合并条件")
        elif milestones:
            set_lifecycle(synced, "development", "in_progress")
    return synced, updated, milestones
