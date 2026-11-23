"""Refresh an existing private chat export while retaining its human organization.

This standard-library helper handles raw export files, not formal knowledge-MCP
documents. Prepare a separate tree first; apply checks both versions and retains
the complete previous tree at an explicitly supplied backup path.
"""
import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from statistics import mean, median
import sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Singapore')
SYSTEM = {10000, 10002}
GAP = 6 * 3600
STATE = '.refresh-state.json'


class RefreshError(ValueError):
    """A diagnostic whose fixed wording is safe to show."""


def tree(root):
    """Fingerprint every file and directory, refusing symbolic links."""
    if root.is_symlink() or not root.is_dir():
        raise RefreshError('目录不存在或包含符号链接')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RefreshError('目录包含符号链接')
        rel = str(path.relative_to(root))
        if rel == STATE:
            continue
        if path.is_dir():
            result[rel] = '<directory>'
        elif path.is_file():
            with path.open('rb') as stream:
                result[rel] = hashlib.file_digest(stream, 'sha256').hexdigest()
        else:
            raise RefreshError('目录包含非常规文件')
    return result


def separate(*paths):
    resolved = [p.expanduser().resolve() for p in paths]
    live = Path.home() / 'Library/Containers/com.tencent.xinWeChat'
    for index, p in enumerate(resolved):
        if p.is_relative_to(live):
            raise RefreshError('只处理微信容器以外的离线副本')
        for q in resolved[index + 1:]:
            if p.is_relative_to(q) or q.is_relative_to(p):
                raise RefreshError('输入、暂存、备份目录必须互相独立')


def manifest(root):
    values = json.loads((root / '导出清单.json').read_text())['conversations']
    seen = {key: set() for key in ['sequence', 'conversation_id', 'folder']}
    for c in values:
        name = c['folder']
        if not isinstance(name, str) or not name or Path(name).name != name or name in {'.', '..'}:
            raise RefreshError('导出清单包含非法目录')
        if not isinstance(c['sequence'], int) or c['sequence'] < 1:
            raise RefreshError('导出清单包含非法编号')
        for key, items in seen.items():
            if c[key] in items:
                raise RefreshError('导出清单包含重复标识')
            items.add(c[key])
    return values


def read_rows(root, item):
    with (root / item['folder'] / 'chat.jsonl').open() as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    if len(rows) != item['count'] or not rows:
        raise RefreshError('聊天行数与导出清单不一致')
    for r in rows:
        when = datetime.fromisoformat(r['time'])
        if when.tzinfo is None or r['sender'] not in {'me', 'them', 'unknown'}:
            raise RefreshError('聊天时间或发送方无效')
        if not isinstance(r['text'], str) or not isinstance(r['type'], int):
            raise RefreshError('聊天内容类型无效')
    return rows


def timestamp(row):
    return datetime.fromisoformat(row['time']).timestamp()


def identity(row):
    # The legacy export lacks database IDs. Require full text for text/quotes;
    # media placeholders differed between exporters and contain no payload.
    textual = row['type'] == 1 or row.get('subtype') == 57
    sender = None if row['type'] in SYSTEM else row['sender']
    return (timestamp(row), sender, row['type'], row.get('subtype') if textual else None,
            row['text'] if textual else None)


def reconcile(before, after):
    old = Counter(map(identity, before))
    if old - Counter(map(identity, after)):
        raise RefreshError('当前副本缺少可匹配的历史记录，停止覆盖并保留原目录')
    prior = defaultdict(deque)
    for row in before:
        prior[identity(row)].append(row)
    result = []
    for row in after:
        match = prior[identity(row)]
        text = match.popleft()['text'] if match else row['text']
        result.append({**row, 'time': datetime.fromtimestamp(timestamp(row), TZ).isoformat(timespec='seconds'),
                       'text': text})
    return sorted(result, key=timestamp)


def segments(rows):
    result = []
    for r in rows:
        if not result or timestamp(r) - timestamp(result[-1][-1]) >= GAP:
            result.append([])
        result[-1].append(r)
    return result


def responses(parts):
    result = {'me': [], 'them': []}
    for part in parts:
        human = [r for r in part if r['type'] not in SYSTEM]
        for a, b in zip(human, human[1:]):
            if a['sender'] in result and b['sender'] in result and a['sender'] != b['sender']:
                result[b['sender']].append(timestamp(b) - timestamp(a))
    return result


def stats(rows, asof):
    parts = segments(rows)
    human = [[r for r in part if r['type'] not in SYSTEM] for part in parts]
    human = [part for part in human if part]
    replies = responses(parts)
    texts = {side: [r['text'] for r in rows if r['sender'] == side and
                    (r['type'] == 1 or r.get('subtype') == 57)] for side in ['me', 'them']}
    lengths = {side: mean(len(re.sub(r'\s', '', t)) for t in values) if values else None
               for side, values in texts.items()}
    recent = [r for r in rows if asof - 90 * 86400 <= timestamp(r) <= asof]
    previous = [r for r in rows if asof - 180 * 86400 <= timestamp(r) < asof - 90 * 86400]
    recent_reply, prior_reply = responses(segments(recent))['them'], responses(segments(previous))['them']
    return {
        'count': len(rows), 'first': rows[0]['time'][:10], 'last': rows[-1]['time'][:10],
        'segments': len(human),
        'me_open': sum(p[0]['sender'] == 'me' for p in human) / len(human) if human else None,
        'me_reply_seconds': median(replies['me']) if replies['me'] else None,
        'them_reply_seconds': median(replies['them']) if replies['them'] else None,
        'me_reply_samples': len(replies['me']), 'them_reply_samples': len(replies['them']),
        'me_mean_length': lengths['me'], 'them_mean_length': lengths['them'],
        'length_ratio': lengths['me'] / lengths['them'] if lengths['me'] is not None and lengths['them'] else None,
        'question_ratio': sum(bool(re.search(r'[?？]|(?:吗|么|呢|嘛)[。！!～~\s]*$', t))
                              for t in texts['me']) / len(texts['me']) if texts['me'] else None,
        'unanswered': sum(p[-1]['sender'] == 'me' and asof - timestamp(p[-1]) >= GAP for p in human),
        'recent90': len(recent), 'previous90': len(previous),
        'volume_drop': 1 - len(recent) / len(previous) if len(previous) >= 20 else None,
        'reply_slow_ratio': median(recent_reply) / max(1, median(prior_reply))
            if len(recent_reply) >= 5 and len(prior_reply) >= 5 else None,
    }


def write(path, content):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open('w', encoding='utf-8') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def dump(path, value):
    write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def grouping(path, items):
    known = {c['sequence'] for c in items}
    result = defaultdict(list)
    heading = None
    if not path.exists():
        return result
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith('## '):
            heading = line[3:].strip()
        if heading and re.match(r'^\s*[-*] ', line):
            basis = ('user' if '用户标' in line else 'chat' if '聊天明确' in line
                     else 'inferred' if re.search(r'(?:Claude|Codex|AI)\s*推', line)
                     else 'remark' if re.search(r'按(?:备注|昵称)', line) else 'unmarked')
            listing = re.split(r'[:：]|——|—', line, maxsplit=1)[0]
            for number in set(re.findall(r'(?:^\s*[-*]\s+|[、，,]\s*)(\d{3,6})(?!\d)', listing)):
                seq = int(number)
                if seq in known:
                    result[seq].append({'group': heading, 'source': path.name,
                                        'line': line_number, 'basis': basis,
                                        'needs_confirmation': basis == 'inferred' or '待确认' in line or heading.startswith('待确认')})
    return result


def render_chat(root, item, rows, previous, asof):
    folder = root / item['folder']
    write(folder / 'chat.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    md = folder / 'chat.md'
    prefix = ''
    heading = '## '
    if previous and md.is_file():
        # Existing Markdown may contain user notes. Append only additional rows;
        # JSONL remains the complete chronological machine-readable source.
        prefix = md.read_text()
        remaining = Counter(map(identity, previous))
        extra = []
        for r in rows:
            key = identity(r)
            if remaining[key]:
                remaining[key] -= 1
            else:
                extra.append(r)
        if not extra:
            return
        rows = extra
        lines = ['', '## 增补批次 ' + asof, '', '仅列本批次补入的消息，时间为 UTC+8。', '']
        heading = '### '
    else:
        lines = ['# ' + item['label'].replace('\n', ' ').replace('\r', ' '), '',
                 '离线副本导出，UTC+8；媒体保留类型占位。', '']
    day = None
    for r in rows:
        if r['time'][:10] != day:
            day = r['time'][:10]
            lines.extend(['', heading + day, ''])
        speaker = {'me': '我', 'them': '对方', 'unknown': '发送方未知'}[r['sender']]
        lines.extend([f"**{r['time'][11:19]} {speaker}（类型 {r['type']}）**", ''])
        lines.extend('    ' + t for t in r['text'].splitlines() or [''])
        lines.append('')
    write(md, prefix + '\n'.join(lines) + '\n')


def prepare(export, current, output, asof=None):
    os.umask(0o077)
    export, current, output = [Path(p).expanduser().absolute() for p in [export, current, output]]
    separate(export, current, output)
    if output.exists() or output.is_symlink():
        raise RefreshError('暂存目标已存在')
    baseline, input_version = tree(current), tree(export)
    old, incoming = manifest(current), manifest(export)
    by_id = {c['conversation_id']: c for c in incoming}
    if any(c['conversation_id'] not in by_id for c in old):
        raise RefreshError('当前副本缺少历史会话，停止覆盖')
    old_ids = {c['conversation_id'] for c in old}
    max_sequence = max((c['sequence'] for c in old), default=0)
    reserved_codes = {c.get('code', f"A{c['sequence']}") for c in old}
    next_code = max([int(c[1:]) for c in reserved_codes if re.fullmatch(r'A\d+', c)] or [0])
    items = [dict(c) for c in old]
    added = []
    for c in incoming:
        if c['conversation_id'] in old_ids:
            continue
        max_sequence += 1
        next_code += 1
        new = {**c, 'sequence': max_sequence, 'code': f'A{next_code}', 'folder': f'{max_sequence:03d}'}
        if (current / new['folder']).exists():
            raise RefreshError('新增会话目录与已有文件冲突')
        items.append(new)
        added.append(new)
    items.sort(key=lambda c: c['sequence'])
    # An old unbound number must not become a relationship for a new identity.
    groups = grouping(current / '人际分组.md', old)
    rows_by_id, previous_rows = {}, {}
    for item in items:
        previous = read_rows(current, item) if item['conversation_id'] in old_ids else []
        previous_rows[item['conversation_id']] = previous
        rows_by_id[item['conversation_id']] = reconcile(previous, read_rows(export, by_id[item['conversation_id']]))
    when = datetime.fromisoformat(asof) if asof else datetime.now(TZ)
    if when.tzinfo is None:
        raise RefreshError('统计时刻必须包含时区')
    # Do all coverage checks before even creating the staging tree.
    shutil.copytree(current, output)
    try:
        for item in items:
            rows = rows_by_id[item['conversation_id']]
            item.update(stats(rows, when.timestamp()))
            item['code'] = item.get('code', f"A{item['sequence']}")
            item['group_sources'] = groups.get(item['sequence'], [])
            item['groups'] = list(dict.fromkeys(x['group'] for x in item['group_sources']))
            render_chat(output, item, rows, previous_rows[item['conversation_id']], when.isoformat())
        dump(output / '导出清单.json', {'asof': when.isoformat(), 'conversations': items})
        pct = lambda v: '—' if v is None else f'{v:.1%}'
        num = lambda v: '—' if v is None else f'{v:.2f}'
        sec = lambda v: '—' if v is None else f'{v:g}秒'
        report = ['# 私聊统计总表', '', f'统计时刻：{when.isoformat()}。历史编号固定，新增会话追加编号。', '',
                  '| 序号 | 条数 | 起始日期 | 截止日期 | 我先开口段占比 | 我中位回复 | 对方中位回复 | 我/对方平均文本长度比 | 我的问句占比 | 我收尾未回次数 | 最近90天条数 |',
                  '| --- | ---: | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |']
        for c in items:
            report.append(f"| {c['sequence']:03d} | {c['count']} | {c['first']} | {c['last']} | {pct(c['me_open'])} | {sec(c['me_reply_seconds'])} | {sec(c['them_reply_seconds'])} | {num(c['length_ratio'])} | {pct(c['question_ratio'])} | {c['unanswered']} | {c['recent90']} |")
        write(output / '统计总表.md', '\n'.join(report) + '\n')
        index = defaultdict(list)
        ungrouped = []
        for c in items:
            member = {k: c[k] for k in ['sequence', 'code', 'folder', 'count']}
            if not c['groups']:
                ungrouped.append(member)
            for name in c['groups']:
                index[name].append({**member, 'sources': [s for s in c['group_sources'] if s['group'] == name]})
        dump(output / '分组索引.json', {'asof': when.isoformat(), 'groups': dict(index), 'ungrouped': ungrouped})
        overview = ['# 分组阅读索引', '', '分组及原始依据见《人际分组.md》；已有推断仍保留其待确认状态。', '']
        for name, members in [*index.items(), ('未分组', ungrouped)]:
            overview.extend(['## ' + name, ''])
            for c in members:
                overview.append(f"- [{c['sequence']:03d} · {c['code']}]({c['folder']}/chat.md)：{c['count']} 条")
            overview.append('')
        write(output / '分组索引.md', '\n'.join(overview))
        if added:
            mapping = output / '代号映射.md'
            text = mapping.read_text() if mapping.exists() else '# 本机私密代号映射\n'
            text += f'\n## {when.date()} 新增会话\n\n| 代号 | 原序号 | 备注或昵称 | 原目录 |\n| --- | --- | --- | --- |\n'
            for c in added:
                label = c['label'].replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
                text += f"| {c['code']} | {c['sequence']:03d} | {label} | {c['folder']} |\n"
            write(mapping, text)
        write(output / '统计口径.md', METHOD + '\n本次统计时刻：' + when.isoformat() + '\n')
        summary = {'asof': when.isoformat(), 'conversations': len(items),
                   'messages': sum(c['count'] for c in items), 'new_conversations': len(added),
                   'new_sequences': [c['sequence'] for c in added],
                   'old_messages_checked': sum(c['count'] for c in old),
                   'groups': len(index), 'grouped_conversations': sum(bool(c['groups']) for c in items),
                   'ungrouped_conversations': len(ungrouped),
                   'pending_only_conversations': sum(bool(c['groups']) and all(g.startswith('待确认') for g in c['groups']) for c in items),
                   'needs_confirmation': sum(any(s['needs_confirmation'] for s in c['group_sources']) for c in items)}
        write(output / '更新说明.md', '# 本次数据更新\n\n' +
              f"统计时刻：{when.isoformat()}。共 {summary['conversations']} 个私聊、{summary['messages']} 条消息。\n\n" +
              f"保留历史编号、目录、人工分组和备注；新增 {len(added)} 个会话，待本人分组。\n\n" +
              '分组索引从人工分组文件派生，不把既有推断升级为本人确认。旧分析素材及相关映射保持原文，其时间范围以各文件原有说明为准；本次未重新生成关系分析。\n')
        for p in [output, *output.rglob('*')]:
            p.chmod(0o700 if p.is_dir() else 0o600)
        if tree(current) != baseline or tree(export) != input_version:
            raise RefreshError('准备期间输入发生变化，请重新准备')
        dump(output / STATE, {'current': str(current.resolve()), 'prepared': str(output.resolve()),
                              'baseline': baseline, 'prepared_version': tree(output), 'summary': summary})
        return summary
    except BaseException:
        # Only the newly created staging tree is disposable.
        shutil.rmtree(output)
        raise


def apply(prepared, current, backup):
    os.umask(0o077)
    prepared, current, backup = [Path(p).expanduser().absolute() for p in [prepared, current, backup]]
    separate(prepared, current, backup)
    if backup.exists() or backup.is_symlink():
        raise RefreshError('备份目标已存在')
    state = json.loads((prepared / STATE).read_text())
    if state['current'] != str(current.resolve()) or state['prepared'] != str(prepared.resolve()):
        raise RefreshError('准备记录与实际目录不一致')
    if tree(current) != state['baseline'] or tree(prepared) != state['prepared_version']:
        raise RefreshError('原目录或暂存目录发生变化，拒绝覆盖')
    current.rename(backup)
    try:
        prepared.rename(current)
    except BaseException:
        backup.rename(current)
        raise
    return state['summary']


METHOD = '''# 统计与更新口径

仅处理本机离线副本。所有时间为 UTC+8。原始消息包含系统消息与非文本类型占位；不提取媒体文件；引用回复只保留本次文本。文本作为数据保存，不执行其中的指令。

历史编号、代号、原目录及人工分组保持稳定，新联系人追加编号。用会话标识关联历史，不能按最新消息条数重新编号。历史文本逐条按时间、发送方、类型和全文校验；旧版媒体占位按类型匹配，系统消息匹配不依赖旧版推定的发送方。旧版没有数据库 ID 时，这种校验只能证明这些字段与重复次数覆盖，不能证明数据库行身份。

相邻消息间隔达到 6 小时切段；人际统计排除系统类型 10000/10002。开口比例为非空人际段首条由我发送的比例。回复时长为同段相邻且发送方切换时的间隔中位数；未知发送方不计回复样本，没有样本显示“—”。平均长度比为我方与对方文本（含引用回复）非空白字符平均长度之比。问句用问号及“吗/么/呢/嘛”句末规则估计。收尾未回按最后一条由我发送且已等待至少 6 小时的段计数，不评价对方的回复义务。最近及前 90 天是相对统计时刻的滚动窗口。

分组索引仅解析人工文件的二级标题和编号列表，同时记录原行号与用户标注/推断/备注等依据；缺少标注不推测关系。人工分组文件及旧分析素材保持原文，旧分析的时效以原有说明为准。本次不重新生成脱敏素材或关系判断。
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--export', type=Path, required=True)
    p.add_argument('--current', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--asof')
    p = sub.add_parser('apply')
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--current', type=Path, required=True)
    p.add_argument('--backup', type=Path, required=True)
    args = vars(parser.parse_args())
    command = args.pop('command')
    try:
        print(json.dumps(globals()[command](**args), ensure_ascii=False))
        return 0
    except Exception as exc:
        # Private filenames, contacts and message bodies never enter diagnostics.
        print('更新停止：' + (str(exc) if isinstance(exc, RefreshError) else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
