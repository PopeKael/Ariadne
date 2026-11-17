# Component: <name>

Status: draft | verified source | verified runtime | legacy | retired  
Last source review: YYYY-MM-DD (branch/commit, if known)  
Primary parcel/owner: <one of Four Parcels, repository/team>

## Purpose and user entry

What user-visible problem does this capability solve? How does a user, scheduler, API, or parent component invoke it?

## Active code map

| Role | Repository-relative path / external location | Notes |
| --- | --- | --- |
| Manifest / registration | | |
| UI or caller | | |
| Adapter / orchestrator | | |
| Implementation | | |
| Configuration | | |
| Focused tests | | |

**Verified call chain:** `caller` → `adapter` → `implementation`.

**Superseded / dead / compatibility code:** name it explicitly, with evidence. Distinguish installed/deployed code from a repository checkout.

## Contracts and safety

Inputs, outputs, APIs, settings, data locations, permissions, destructive actions, confirmation requirements, skip/failure policy, and integration boundaries. Link canonical contracts where they already exist.

## Verification and recovery

- Safe preview or test command:
- Expected result:
- Runtime/platform prerequisites:
- How to revert the component change without altering user data:
- What has actually been executed and what remains unverified:

## Decisions and change history

Link dated architecture decisions, relevant PRs/issues/commits, and migration notes. Explain *why* a notable choice was made **only when evidence supports it**. Git commit history is the authoritative chronology; never invent one.

## Maintenance checklist

When this component changes, revalidate the call chain, paths, contracts, tests, safety behaviour, and links from the [Engineering Catalogue](../COMPONENT-CATALOGUE.md).
