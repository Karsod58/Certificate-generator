# Contributing to Bulk Certificate Generator

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to this project.

## Development Setup

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/yourusername/Certificate_generator.git
   cd Certificate_generator
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Start Redis**
   ```bash
   docker-compose up -d redis
   ```

5. **Run tests to verify setup**
   ```bash
   pytest tests/ -v
   ```

## Code Style

- Follow PEP 8 style guidelines
- Use type hints where appropriate
- Write docstrings for all public functions and classes
- Keep functions focused (single responsibility principle)

## Testing

- **Write tests for all new features**
- **Maintain or improve test coverage**
- Run tests before submitting PR: `pytest tests/ -v`
- All tests must pass

### Test Structure
```
tests/
├── conftest.py              # Shared fixtures
├── test_jobs.py             # Job creation and status
├── test_processing.py       # Core processing logic
├── test_celery_integration.py  # Celery wiring
└── test_downloads.py        # Download endpoints
```

## Commit Messages

Use clear, descriptive commit messages:

```
feat: add rate limiting support
fix: handle missing certificate files correctly
docs: update API documentation
test: add tests for edge cases in validation
refactor: simplify job status computation
```

## Pull Request Process

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Write code
   - Add tests
   - Update documentation if needed

3. **Run tests**
   ```bash
   pytest tests/ -v
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat: your feature description"
   ```

5. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request**
   - Provide a clear description
   - Reference any related issues
   - Wait for review

## What to Contribute

### Good First Issues
- Add more PDF template options
- Improve error messages
- Add input validation edge cases
- Expand documentation with examples

### Feature Ideas
- S3/Azure Blob storage support
- Custom fonts and templates
- Email delivery integration
- Admin dashboard
- Webhook notifications
- Multi-language support

### Bug Fixes
Always welcome! Please include:
- Description of the bug
- Steps to reproduce
- Expected vs actual behavior
- Test case demonstrating the fix

## Questions?

Open an issue or discussion on GitHub. We're happy to help!

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
