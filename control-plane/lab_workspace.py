"""Owned single-file workspace; exact patches never execute code or address OS paths."""
import json


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
                raise ValueError('Patch search must match exactly once; no edits applied.')
            code = code.replace(search,replacement,1)
        if code == self.code: raise ValueError('Patch made no change.')
        return self.candidate({**self.project,'files':[{'path':'index.html','content':code}]},patch)
