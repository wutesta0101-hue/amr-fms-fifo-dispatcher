#!/usr/bin/env python3
"""M2 步驟 2：VDA5050 訊息的 schema 一致性檢查

用官方 schema 驗證「我們實際發出去的訊息」，不是驗證手寫的範例——
這是「我符合規範」的可執行證據，而不是口頭宣稱。

刻意只相依 jsonschema（不需要 ROS、不需要 paho）：訊息由 mosquitto_sub 抓下來
存成檔案，一行一則 JSON，本腳本只負責驗證。這樣它可以跑在乾淨的 venv 裡，
不必動到系統 Python（那台機器有過 conda 與 websockets 的前科）。

用法：
    mosquitto_sub -h localhost -t 'vda5050/v3/rmfdemos/tinyRobot1/state' -C 10 > /tmp/state.jsonl
    python3 tools/vda5050_schema_check.py state /tmp/state.jsonl

結束碼：0＝全部通過，1＝有訊息不符，2＝參數或檔案問題（可直接當驗證信號用）
"""

import json
import pathlib
import sys

import jsonschema

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent.parent / 'schemas'
KINDS = ('order', 'state', 'connection')


# jsonschema 4.x 已棄用 `jsonschema.__version__`（直接讀會噴 DeprecationWarning，
# 把驗證結果沖散）。改走 importlib.metadata，舊版（3.2.0）沒有時再退回舊屬性。
def jsonschema_version():
    try:
        from importlib.metadata import version
        return version('jsonschema')
    except Exception:
        return getattr(jsonschema, '__version__', '未知')


# 逐行讀入 mosquitto_sub 的輸出；空行與 mosquitto_sub -v 的 "topic {json}" 都能吃
def load_messages(path):
    messages = []
    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith('{'):          # -v 模式會在前面加上 topic
            line = line[line.find('{'):] if '{' in line else ''
        if not line:
            continue
        try:
            messages.append((lineno, json.loads(line)))
        except json.JSONDecodeError as err:
            print(f'  ✗ 第 {lineno} 行不是合法 JSON：{err}')
            messages.append((lineno, None))
    return messages


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        print(f'用法：{sys.argv[0]} {{{"|".join(KINDS)}}} <訊息檔>')
        return 2

    kind, path = sys.argv[1], sys.argv[2]
    schema_path = SCHEMA_DIR / f'{kind}.schema'
    if not schema_path.exists():
        print(f'找不到 schema：{schema_path}')
        return 2
    if not pathlib.Path(path).is_file():
        print(f'找不到訊息檔：{path}')
        return 2

    schema = json.load(open(schema_path, encoding='utf-8'))
    cls = jsonschema.validators.validator_for(schema)
    validator = cls(schema)

    # 這行是刻意印出來的：jsonschema < 4.18 不認得 draft 2020-12，會靜靜退回
    # Draft7 驗證。不印出來的話，「通過」這個結論會比實際情況更強。
    print(f'jsonschema {jsonschema_version()}｜schema 宣告 '
          f'{schema.get("$schema", "（未宣告）")}｜實際使用 {cls.__name__}')
    if cls.__name__ != 'Draft202012Validator':
        print('  ⚠️ 未以 2020-12 驗證，結論僅涵蓋兩版共通的規則'
              '（required／type／enum）。升級 jsonschema ≥4.18 後重跑可消除此折扣。')

    messages = load_messages(path)
    if not messages:
        print(f'{path} 裡沒有任何訊息')
        return 2

    failed = 0
    for lineno, msg in messages:
        if msg is None:
            failed += 1
            continue
        errors = sorted(validator.iter_errors(msg), key=str)
        if errors:
            failed += 1
            print(f'  ✗ 第 {lineno} 行（headerId={msg.get("headerId")}）：')
            for e in errors:
                where = '/'.join(str(p) for p in e.absolute_path) or '(根)'
                print(f'      {where}：{e.message}')

    total = len(messages)
    print(f'{total - failed}/{total} 則 {kind} 通過 schema')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
