import ast
import hashlib
import json
from pathlib import Path

def test_frozen_functions_are_unchanged():
    directory = Path(__file__).parent
    expected = json.loads((directory/'frozen_inference.json').read_text())
    source = (directory.parent/'main.py').read_text(encoding='utf-8')
    actual = {node.name: hashlib.sha256(ast.get_source_segment(source,node).encode()).hexdigest()
              for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in expected}
    assert actual == expected
