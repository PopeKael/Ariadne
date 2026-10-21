'use strict';

const path = require('node:path');
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');

const inputFileIndex = process.argv.indexOf('--input-file');
const inputFile = inputFileIndex >= 0 ? process.argv[inputFileIndex + 1] : null;
const rootArgument = inputFileIndex >= 0 ? process.argv[inputFileIndex + 2] : process.argv[2];
const root = path.resolve(rootArgument || '');
const deny = (reason) => {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }));
  process.exit(0);
};

const inside = (candidate) => {
  const resolved = path.resolve(root, candidate);
  const relative = path.relative(root, resolved);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
};

const evaluate = (raw) => {
  try {
    const event = JSON.parse(raw);
    const input = event.tool_input && typeof event.tool_input === 'object' ? event.tool_input : {};
    const tool = String(event.tool_name || '').toLowerCase();

    if (!root || path.resolve(String(event.cwd || '')) !== root) {
      return deny('Qwen Code tool rejected: session cwd does not match the assigned Ariadne workspace.');
    }

    if (['read_file', 'write_file', 'edit', 'notebook_edit'].includes(tool)) {
      const filePath = input.file_path ?? input.path;
      if (typeof filePath !== 'string' || !inside(filePath)) {
        return deny('Qwen Code tool rejected: file path is missing, absolute, or outside the assigned Ariadne workspace.');
      }
    } else if (tool === 'glob' || tool === 'grep_search') {
      const target = input.path ?? input.directory_path ?? '.';
      const pattern = input.pattern ?? input.glob_pattern ?? '';
      if (typeof target !== 'string' || (target !== '.' && !inside(target)) || (typeof pattern === 'string' && (path.isAbsolute(pattern) || pattern.split(/[\\/]/).includes('..')))) {
        return deny('Qwen Code tool rejected: search path or pattern escapes the assigned Ariadne workspace.');
      }
    } else if (tool === 'run_shell_command') {
      const command = String(input.command || '').trim();
      const match = /^(?:"([^"]+)"|([^\s"]+))\s+(?:"([^"]+\.py)"|([A-Za-z0-9_.-]+\.py)|([A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]+\.py))$/i.exec(command);
      const executable = match && (match[1] || match[2]);
      const scriptPath = match && (match[3] || match[4] || match[5]);
      const configuredPython = String(process.env.ARIADNE_QWEN_PYTHON || '').trim();
      const barePython = executable && /^(?:python|python\.exe)$/i.test(executable);
      let exactConfiguredPython = false;
      if (executable && configuredPython && path.isAbsolute(configuredPython)) {
        try {
          exactConfiguredPython = path.resolve(executable).toLowerCase() === path.resolve(configuredPython).toLowerCase();
        } catch (_) {
          exactConfiguredPython = false;
        }
      }
      if ((!barePython && !exactConfiguredPython) || !scriptPath || !inside(scriptPath)) {
        return deny('Qwen Code tool rejected: only one Python file contained inside the assigned workspace may run in this isolated coding pilot.');
      }
      const script = path.resolve(root, scriptPath);
      const validator = String.raw`
import ast, pathlib, sys
tree = ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
allowed = (ast.Module, ast.Expr, ast.Call, ast.Name, ast.Load, ast.Constant)
for node in ast.walk(tree):
    if not isinstance(node, allowed):
        raise SystemExit(2)
    if isinstance(node, ast.Name) and node.id != 'print':
        raise SystemExit(3)
    if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id != 'print' or node.keywords):
        raise SystemExit(4)
`;
      const result = spawnSync(exactConfiguredPython ? configuredPython : 'python', ['-I', '-c', validator, script], {
        cwd: root,
        encoding: 'utf8',
        timeout: 5000,
        windowsHide: true,
      });
      if (result.error || result.status !== 0) {
        return deny('Qwen Code tool rejected: Python script failed the isolated print-only AST policy.');
      }
    } else {
      return deny(`Qwen Code tool rejected: tool '${tool || '(unnamed)'}' is not enabled for this isolated coding pilot.`);
    }

    process.stdout.write(JSON.stringify({ hookSpecificOutput: { permissionDecision: 'allow' } }));
  } catch (error) {
    deny(`Qwen Code tool rejected: guard error (${error.message}).`);
  }
};

if (inputFile) {
  evaluate(fs.readFileSync(inputFile, 'utf8'));
} else {
  let raw = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { raw += chunk; });
  process.stdin.on('end', () => evaluate(raw));
}
