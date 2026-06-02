import asyncio
import asyncpg
import bcrypt

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/nommora_db"

async def reset():
    conn = await asyncpg.connect(DATABASE_URL)
    # On génère un nouveau hash propre pour le mot de passe "password123"
    hashed = bcrypt.hashpw("password123".encode(), bcrypt.gensalt()).decode()
    try:
        # On force la mise à jour pour Alice et Marc
        await conn.execute(
            "UPDATE users SET password_hash = $1 WHERE email IN ('alice@nommora.com', 'marc@nommora.com')", 
            hashed
        )
        print("✅ Les mots de passe d'Alice et Marc ont été réinitialisés à : password123")
    except Exception as e:
        print(f"❌ Erreur : {e}")
    finally:
        await conn.close()

asyncio.run(reset())
