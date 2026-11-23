import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/wechat-mac-export/scripts/refresh_knowledge.py'
spec = importlib.util.spec_from_file_location('refresh_knowledge', SCRIPT)
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def row(text='原消息', kind=1, sender='me'):
    return {'time': '2026-11-01T00:00:00+08:00', 'sender': sender,
            'type': kind, 'subtype': None, 'text': text}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = self.root / 'current'
        self.export = self.root / 'export'
        self.stage = self.root / 'prepared'
        self.backup = self.root / 'backup'
        self.old = {'sequence': 1, 'code': 'A1', 'conversation_id': 'peer',
                    'folder': '001-保留用户目录', 'label': '原备注', 'count': 1}
        write_json(self.current / '导出清单.json', {'asof': '2026-11-01', 'conversations': [self.old]})
        self.chat(self.current, self.old, [row()])
        (self.current / '人际分组.md').write_text('# 人际分组\n\n## 家人\n- 001 备注——用户标\n')
        (self.current / '代号映射.md').write_text('原有代号和用户备注\n')
        (self.current / '人工备注.md').write_text('原样保留\n')
        self.peer = {**self.old, 'sequence': 2, 'folder': '002', 'count': 2, 'label': '新版昵称'}
        self.new = {'sequence': 1, 'conversation_id': 'new-peer', 'folder': '001',
                    'label': '新联系人', 'count': 3}
        write_json(self.export / '导出清单.json', {'conversations': [self.new, self.peer]})
        self.chat(self.export, self.peer, [row(), row('新增消息')])
        self.chat(self.export, self.new, [row('新会话')] * 3)

    def chat(self, root, item, rows):
        p = root / item['folder'] / 'chat.jsonl'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        (p.parent / 'chat.md').write_text('已有正文\n')

    def prepare(self):
        return refresh.prepare(self.export, self.current, self.stage, '2026-11-23T12:00:00+08:00')

    def test_preserves_identity_folders_and_notes_and_leaves_new_people_ungrouped(self):
        result = self.prepare()
        manifest = json.loads((self.stage / '导出清单.json').read_text())['conversations']
        peer, new = manifest
        self.assertEqual((peer['sequence'], peer['code'], peer['folder'], peer['label']),
                         (1, 'A1', self.old['folder'], self.old['label']))
        self.assertEqual((new['sequence'], new['code']), (2, 'A2'))
        self.assertEqual(peer['groups'], ['家人'])
        self.assertEqual(new['groups'], [])
        self.assertEqual(result['new_conversations'], 1)
        self.assertEqual(result['messages'], 5)
        for name in ['人际分组.md', '人工备注.md']:
            self.assertEqual((self.stage / name).read_bytes(), (self.current / name).read_bytes())
        self.assertTrue((self.stage / '代号映射.md').read_text().startswith('原有代号和用户备注\n'))
        self.assertEqual(peer['count'], 2)
        self.assertIn('2026-11-23', (self.stage / '统计总表.md').read_text())
        self.assertEqual((self.current / self.old['folder'] / 'chat.md').read_text(), '已有正文\n')
        self.assertTrue(all((p.stat().st_mode & 0o077) == 0 for p in self.stage.rglob('*')))

    def test_rejects_missing_old_text_and_retains_current_tree(self):
        self.chat(self.export, self.peer, [row('被替换的消息'), row('新增消息')])
        with self.assertRaisesRegex(ValueError, '历史'):
            self.prepare()
        self.assertTrue((self.current / self.old['folder'] / 'chat.jsonl').exists())
        self.assertFalse(self.stage.exists())

    def test_compares_media_by_type_but_keeps_original_historical_representation(self):
        self.chat(self.current, self.old, [row('[图片:3]', 3)])
        self.chat(self.export, self.peer, [row('[图片]', 3), row('新增消息')])
        self.prepare()
        rows = [json.loads(x) for x in (self.stage / self.old['folder'] / 'chat.jsonl').read_text().splitlines()]
        self.assertEqual(rows[0]['text'], '[图片:3]')
        self.assertEqual(len(rows), 2)

    def test_apply_checks_version_and_preserves_concurrent_user_edits(self):
        self.prepare()
        (self.current / '人际分组.md').write_text('用户刚改了分组')
        with self.assertRaisesRegex(ValueError, '变化'):
            refresh.apply(self.stage, self.current, self.backup)
        self.assertFalse(self.backup.exists())
        self.assertEqual((self.current / '人际分组.md').read_text(), '用户刚改了分组')

    def test_apply_keeps_backup_and_repeated_refresh_adds_no_duplicate_aliases(self):
        self.prepare()
        refresh.apply(self.stage, self.current, self.backup)
        self.assertEqual((self.backup / '导出清单.json').read_text(),
                         json.dumps({'asof': '2026-11-01', 'conversations': [self.old]}, ensure_ascii=False))
        before = (self.current / '代号映射.md').read_bytes()
        result = self.prepare()
        self.assertEqual(result['new_conversations'], 0)
        self.assertEqual((self.stage / '代号映射.md').read_bytes(), before)

    def test_rejects_source_symlinks_and_manifest_path_traversal(self):
        (self.current / '外链').symlink_to(self.export, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare()
        (self.current / '外链').unlink()
        self.peer['folder'] = '../outside'
        write_json(self.export / '导出清单.json', {'conversations': [self.peer]})
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.stage.exists())

    def test_rejects_a_modified_prepared_tree(self):
        self.prepare()
        (self.stage / '人工备注.md').write_text('意外覆盖')
        with self.assertRaisesRegex(ValueError, '变化'):
            refresh.apply(self.stage, self.current, self.backup)
        self.assertFalse(self.backup.exists())

    def test_new_contact_does_not_inherit_an_unbound_old_number(self):
        with (self.current / '人际分组.md').open('a') as f:
            f.write('\n## 朋友\n- 002 一个尚未绑定身份的编号\n')
        self.prepare()
        items = json.loads((self.stage / '导出清单.json').read_text())['conversations']
        self.assertEqual(items[1]['groups'], [])

    def test_retains_chat_evidence_and_pending_inference_as_distinct_sources(self):
        (self.current / '人际分组.md').write_text(
            '## 同学\n- 001 同学关系——聊天明确\n'
            '## 朋友\n- 001 朋友关系——AI 推，待确认\n')
        self.prepare()
        item = json.loads((self.stage / '导出清单.json').read_text())['conversations'][0]
        chat, inference = item['group_sources']
        self.assertEqual((chat['basis'], chat['needs_confirmation']), ('chat', False))
        self.assertEqual((inference['basis'], inference['needs_confirmation']), ('inferred', True))

    def test_group_parser_ignores_numbers_in_explanations_but_keeps_explicit_lists(self):
        second = {**self.new, 'sequence': 2, 'code': 'A2', 'folder': '002-旧联系人'}
        write_json(self.current / '导出清单.json', {'conversations': [self.old, second]})
        self.chat(self.current, second, [row('新会话')] * 3)
        (self.current / '人际分组.md').write_text('## 同学\n- 001 同学：曾帮助002处理材料\n')
        self.prepare()
        items = json.loads((self.stage / '导出清单.json').read_text())['conversations']
        self.assertEqual(items[1]['groups'], [])
        import shutil
        shutil.rmtree(self.stage)
        (self.current / '人际分组.md').write_text('## 同学\n- 001 同学、002 同学——聊天明确\n')
        self.prepare()
        items = json.loads((self.stage / '导出清单.json').read_text())['conversations']
        self.assertEqual(items[1]['groups'], ['同学'])

    def test_preserves_manual_annotations_inside_existing_chat_markdown(self):
        note = '已有聊天正文\n\n## 我自己的备注\n这一段不能丢。\n'
        (self.current / self.old['folder'] / 'chat.md').write_text(note)
        self.prepare()
        updated = (self.stage / self.old['folder'] / 'chat.md').read_text()
        self.assertTrue(updated.startswith(note))
        self.assertIn('新增消息', updated)

    def test_legacy_ai_inference_stays_unconfirmed_without_an_explicit_pending_suffix(self):
        (self.current / '人际分组.md').write_text('## 朋友\n- 001 朋友——Claude 推\n')
        self.prepare()
        item = json.loads((self.stage / '导出清单.json').read_text())['conversations'][0]
        self.assertTrue(item['group_sources'][0]['needs_confirmation'])


if __name__ == '__main__':
    unittest.main()
