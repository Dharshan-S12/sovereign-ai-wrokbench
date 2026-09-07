import asyncio
import asyncpg

async def migrate():
    conn = await asyncpg.connect("postgresql://ai_user:ai_password@localhost:5432/sovereign_db")
    try:
        await conn.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'processing'")
        print("MIGRATION SUCCESSFUL: 'processing' added to taskstatus enum.")
    except Exception as e:
        print(f"Error migrating enum: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(migrate())
