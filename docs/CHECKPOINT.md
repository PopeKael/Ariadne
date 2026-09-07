# Ariadne project checkpoint

`CHECKPOINT` means that the desktop remains authoritative while the latest
intentional shareable project work is committed and mirrored to GitHub.

The canonical implementation is:

```powershell
& 'D:\Downloads\KnowledgeVault\00_System\Commit.ps1'
```

It operates on both independent repositories:

- Ariadne: `main` -> `PopeKael/Ariadne`
- KnowledgeVault: `knowledge-vault` -> `PopeKael/KnowledgeVault`

The checkpoint classifies local changes, commits only shareable work, and
preserves ignored, private, generated, and machine-local material. A dirty
tree is acceptable only when every remaining change is explicitly classified
as local-only. Completion requires a fresh fetch and exact local/remote HEAD,
ahead, and behind verification for both repositories.

`REMOTE COMMITS PUSHED` describes a transport result. `PROJECT CHECKPOINT
COMPLETE` is the stronger result that makes the GitHub mirror safe for the
Browser ChatGPT handoff.
