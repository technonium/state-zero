# Security

Do not commit credentials or personal data.

Sensitive values include:

- `.env`
- WHOOP access or refresh tokens
- Instagram/Meta access tokens
- Telegram bot tokens
- Google/OpenRouter API keys
- private astrology files
- runtime state and output folders

Before publishing a fork or archive, run:

```bash
python3 ops/check_repo_hygiene.py --check-working-tree --check-ignored
git log --all -- .env
```

If a real secret was committed at any point, rotate it. Git history can retain old values even after the current file is deleted.

If you find a security issue in this project, please avoid posting live secrets
or exploit details in a public issue. Use GitHub private vulnerability reporting
if it is enabled on the repository, or contact the maintainer directly.
