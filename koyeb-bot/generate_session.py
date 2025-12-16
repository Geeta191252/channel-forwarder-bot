"""
SESSION STRING GENERATOR
=========================
Ye script run karo apne computer pe session string generate karne ke liye.

Requirements:
    pip install pyrogram tgcrypto

Usage:
    python generate_session.py
"""

import asyncio

try:
    from pyrogram import Client
except ImportError:
    print("❌ Pyrogram install nahi hai!")
    print("Ye command run karo: pip install pyrogram tgcrypto")
    exit(1)


async def main():
    print("=" * 50)
    print("   TELEGRAM SESSION STRING GENERATOR")
    print("=" * 50)
    print()
    print("Pehle https://my.telegram.org se API credentials lo")
    print()
    
    # Get API credentials
    api_id = input("API_ID dalo (number): ").strip()
    api_hash = input("API_HASH dalo (text): ").strip()
    
    if not api_id or not api_hash:
        print("❌ API_ID aur API_HASH dono required hai!")
        return
    
    try:
        api_id = int(api_id)
    except:
        print("❌ API_ID sirf number hona chahiye!")
        return
    
    print()
    print("Ab Telegram login hoga...")
    print("Phone number dalo jab puche (with country code: +91...)")
    print()
    
    try:
        async with Client(":memory:", api_id=api_id, api_hash=api_hash) as app:
            session_string = await app.export_session_string()
            
            print()
            print("=" * 50)
            print("✅ SUCCESS! Ye hai aapka SESSION_STRING:")
            print("=" * 50)
            print()
            print(session_string)
            print()
            print("=" * 50)
            print()
            print("⚠️  IMPORTANT:")
            print("- Ye string kisi ko share mat karo!")
            print("- Isko Koyeb environment variables me dalo")
            print("- Variable name: SESSION_STRING")
            print()
            
            # Save to file
            with open("session_string.txt", "w") as f:
                f.write(session_string)
            print("📁 session_string.txt file me bhi save ho gaya")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        print()
        print("Common issues:")
        print("- Phone number galat format me dala")
        print("- Code expire ho gaya")
        print("- API credentials galat hai")


if __name__ == "__main__":
    asyncio.run(main())
