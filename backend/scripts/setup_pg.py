import asyncio
import asyncpg

async def setup():
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        user="postgres",
        database="postgres"
    )
    
    user_exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'ai_user'")
    if not user_exists:
        await conn.execute("CREATE ROLE ai_user WITH LOGIN SUPERUSER PASSWORD 'ai_password'")
        print("Created user ai_user")
    else:
        await conn.execute("ALTER ROLE ai_user WITH LOGIN SUPERUSER PASSWORD 'ai_password'")
        print("Updated user ai_user")

    db_exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'sovereign_db'")
    if not db_exists:
        await conn.execute("CREATE DATABASE sovereign_db OWNER ai_user")
        print("Created database sovereign_db")
    else:
        print("Database sovereign_db already exists")
        
    await conn.close()
    
    test_conn = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        user="ai_user",
        password="ai_password",
        database="sovereign_db"
    )
    print("SUCCESS: Connected as ai_user to sovereign_db on port 5432!")
    await test_conn.close()

if __name__ == "__main__":
    asyncio.run(setup())
