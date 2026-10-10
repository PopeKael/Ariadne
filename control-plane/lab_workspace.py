"""Owned single-file workspace; exact patches never execute code or address OS paths."""
import json
import difflib


def source_window(code, start_line=1, end_line=None, offset=0, size=6000):
    """Exact source, with locations outside the text so it can be copied to patch."""
    lines = code.splitlines(keepends=True)
    start_line = min(start_line, max(1,len(lines)))
    end_line = min(end_line or start_line+79, len(lines))
    selected = ''.join(lines[start_line-1:end_line])
    content = selected[offset:offset+size]
    return {'start_line':start_line,'end_line':end_line,'offset':offset,
            'content':content,'total_lines':len(lines),
            'truncated':offset+len(content)<len(selected),
            'next_offset':offset+len(content) if offset+len(content)<len(selected) else None}


class PatchMatchError(ValueError):
    def __init__(self, message, snippets):
        super().__init__(message)
        self.details = {'snippets':snippets,'source_note':'Actual source before this atomic patch; no edits applied.'}


class Workspace:
    def __init__(self, directory, record, atomic_write, validate):
        self.directory, self.record, self.write, self.validate = directory, record, atomic_write, validate
        self.code = None
        self.project = None

    def candidate(self, project, patch=None):
        project = self.validate(project)
        code = project['files'][0]['content']
        n = len(self.record['attempts'])
        self.write(self.directory/f'attempt-{n}.html',code)
        self.write(self.directory/'index.html',code)
        if patch is not None: self.write(self.directory/f'patch-{n}.json',json.dumps(patch,ensure_ascii=False,indent=2))
        self.code, self.project = code, project
        self.record['attempts'].append({'attempt':n,'failures':[]})
        self.record.update(project_path=str(self.directory),project_name=project['project_name'],summary=project['summary'])
        return code

    def patch(self, patch):
        edits = patch.get('edits')
        if set(patch) != {'edits'} or not isinstance(edits,list) or not 1 <= len(edits) <= 12:
            raise ValueError('Expected 1–12 bounded exact edits.')
        code = self.code
        for edit in edits:
            if set(edit) != {'path','search','replace'} or edit['path'] != 'index.html':
                raise ValueError('Patch must target owned index.html only.')
            search, replacement = edit['search'], edit['replace']
            if not isinstance(search,str) or not search or not isinstance(replacement,str) or len(search)+len(replacement)>16000:
                raise ValueError('Invalid or oversized patch.')
            if code.count(search) != 1:
                matches = code.count(search)
                # Report the committed source, even if an earlier edit in this
                # atomic batch matched. Intermediate text was never written.
                actual = self.code
                if search in actual:
                    positions = []
                    pos = actual.find(search)
                    while pos >= 0 and len(positions)<3:
                        positions.append(pos)
                        pos = actual.find(search,pos+len(search))
                else:
                    lines = actual.splitlines(keepends=True)
                    needles = [line.strip() for line in search.splitlines() if line.strip()]
                    ranked, base = [], 0
                    for line in lines:
                        for needle in needles:
                            matcher = difflib.SequenceMatcher(None,needle,line,autojunk=False)
                            match = matcher.find_longest_match()
                            score = max(matcher.ratio(),match.size/max(1,len(needle)))
                            ranked.append((score,base+match.b))
                        base += len(line)
                    positions = list(dict.fromkeys(pos for score,pos in sorted(ranked,reverse=True)
                                                   if score>=.35))[:3]
                snippets = []
                context = []
                for pos in positions:
                    line = actual[:pos].count('\n')+1
                    # Offset also supports minified single-line applications.
                    start = max(1,line-2)
                    line_start = actual.rfind('\n',0,pos)+1
                    block_start = sum(len(s) for s in actual.splitlines(keepends=True)[:start-1])
                    window = source_window(actual,start,line+2,
                        max(0,pos-block_start-200),2000)
                    snippets.append(window)
                    context.append({'line':line,'text':actual[line_start:line_start+180]})
                raise PatchMatchError('Patch search must match exactly once; no edits applied. '
                                 + json.dumps({'matches':matches,'search':search[:180],'closest_actual_lines':context},ensure_ascii=False)
                                 + ' Copy exact source from read; do not repeat this unmatched search.',snippets)
            code = code.replace(search,replacement,1)
        if code == self.code: raise ValueError('Patch made no change.')
        return self.candidate({**self.project,'files':[{'path':'index.html','content':code}]},patch)
