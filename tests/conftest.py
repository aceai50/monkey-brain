import os

os.environ.update({
    "MONGODB_URI": "mongodb://localhost:27017", "MONGODB_DB": "test",
    "JWT_SECRET": "x" * 40, "ADMIN_EMAILS": "boss@example.com", "MAX_USERS": "3",
    "EMAIL_PROVIDER": "console", "INTERNAL_CRON_SECRET": "s" * 32, "GOOGLE_CLIENT_ID": "",
    "GEMINI_API_KEY": "", "TRUST_PROXY": "false",
})
