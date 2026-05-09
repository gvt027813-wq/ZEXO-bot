import asyncio, os, sys, re
from telethon import TelegramClient, functions, types, errors, events
from telethon.tl.types import KeyboardButtonCallback, ReplyInlineMarkup, KeyboardButtonRow

# --- CONFIGURATION ---
API_ID = 33205239
API_HASH = "d0e638a6c56bda91cd0ce4659d00a6b9"
BOT_TOKEN = "8555961488:AAHRYoBqJDgR-PfV0LeFRjJBvVNDBeEtpVU"
OWNER_ID = 8161593137
SESSION_DIR = './sessions'

class ZexoV12:
    def __init__(self):
        self.bot = TelegramClient('bot_control', API_ID, API_HASH)
        self.workers = []
        self.waiting_for = {} # User input tracking

    async def load_workers(self):
        self.workers = []
        if not os.path.exists(SESSION_DIR): os.makedirs(SESSION_DIR)
        files = [f for f in os.listdir(SESSION_DIR) if f.endswith('.session')]
        for f in files:
            if 'bot_control' in f: continue
            cl = TelegramClient(os.path.join(SESSION_DIR, f.replace('.session', '')), API_ID, API_HASH)
            try:
                await cl.connect()
                if await cl.is_user_authorized(): self.workers.append(cl)
            except: pass
        return len(self.workers)

    async def get_main_menu(self):
        return [
            [KeyboardButtonCallback("🛰️ JOIN/LEAVE", b"cat_join"), KeyboardButtonCallback("⚔️ RAID/SPAM", b"cat_raid")],
            [KeyboardButtonCallback("📈 BOOST/VIEW", b"cat_boost"), KeyboardButtonCallback("🎭 STEALTH/BIO", b"cat_stealth")],
            [KeyboardButtonCallback("🛡️ ADMIN/REPORT", b"cat_admin"), KeyboardButtonCallback("🔄 REFRESH", b"sync")],
            [KeyboardButtonCallback("🛑 STOP SYSTEM", b"retire")]
        ]

    async def start(self):
        await self.bot.start(bot_token=BOT_TOKEN)
        await self.load_workers()
        
        print(f"✅ ZEXO V12 BUTTON EDITION IS ONLINE")

        @self.bot.on(events.NewMessage(pattern='/start', from_users=OWNER_ID))
        async def start_handler(event):
            await event.reply(
                f"🚀 **ZEXO V12 CONTROL HUB**\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 **Status:** Online\n🛰️ **Workers Active:** `{len(self.workers)}`\n"
                f"👤 **Owner:** `{OWNER_ID}`\n━━━━━━━━━━━━━━━━━━━━\n"
                f"Choose a category from the buttons below:",
                buttons=await self.get_main_menu()
            )

        @self.bot.on(events.CallbackQuery)
        async def callback_handler(event):
            if event.sender_id != OWNER_ID: return
            data = event.data

            # --- CATEGORY MENUS ---
            if data == b"cat_join":
                btns = [[KeyboardButtonCallback("➕ Mass Join", b"act_join"), KeyboardButtonCallback("➖ Mass Leave", b"act_leave")], [KeyboardButtonCallback("🔙 Back", b"back")]]
                await event.edit("🛰️ **JOIN & LEAVE TOOLS**", buttons=btns)

            elif data == b"cat_raid":
                btns = [[KeyboardButtonCallback("🔥 Hyper Spam", b"act_spam"), KeyboardButtonCallback("📞 Voice Raid", b"act_vraid")], [KeyboardButtonCallback("🔙 Back", b"back")]]
                await event.edit("⚔️ **RAID & SPAM TOOLS**", buttons=btns)

            elif data == b"cat_boost":
                btns = [[KeyboardButtonCallback("🔥 Mass React", b"act_react"), KeyboardButtonCallback("👁️ View Boost", b"act_view")], [KeyboardButtonCallback("🔙 Back", b"back")]]
                await event.edit("📈 **ENGAGEMENT BOOST TOOLS**", buttons=btns)

            elif data == b"cat_stealth":
                btns = [[KeyboardButtonCallback("🟢 All Online", b"act_online"), KeyboardButtonCallback("✍️ Edit Bio", b"act_bio")], [KeyboardButtonCallback("🔙 Back", b"back")]]
                await event.edit("🎭 **STEALTH & IDENTITY TOOLS**", buttons=btns)

            # --- ACTIONS ---
            elif data == b"sync":
                count = await self.load_workers()
                await event.answer(f"🔄 Database Synced! {count} Workers Active.", alert=False)
                await event.edit(f"🚀 **ZEXO V12 CONTROL HUB**\nWorkers: `{count}`", buttons=await self.get_main_menu())

            elif data == b"act_online":
                for cl in self.workers:
                    await cl(functions.account.UpdateStatusRequest(offline=False))
                await event.answer("🟢 All IDs are now ONLINE!", alert=True)

            elif data == b"back":
                await event.edit("🚀 **MAIN CONTROL HUB**", buttons=await self.get_main_menu())

            elif data == b"act_join":
                self.waiting_for[OWNER_ID] = "join_link"
                await event.reply("🔗 **Send the Channel Link/Username to Join:**")

            elif data == b"act_spam":
                self.waiting_for[OWNER_ID] = "spam_data"
                await event.reply("🔥 **Send Target Username & Message (Format: @username | Message):**")

            elif data == b"retire":
                await event.edit("🛑 **RETIRED.** All connections closed.")
                for cl in self.workers: await cl.disconnect()
                await self.bot.disconnect()
                sys.exit()

        # --- INPUT HANDLER FOR BUTTON ACTIONS ---
        @self.bot.on(events.NewMessage(from_users=OWNER_ID))
        async def input_handler(event):
            if OWNER_ID not in self.waiting_for: return
            
            mode = self.waiting_for[OWNER_ID]
            text = event.raw_text

            if mode == "join_link":
                await event.reply(f"🛰️ Joining `{text}` with {len(self.workers)} IDs...")
                clean = text.replace("https://t.me/", "").replace("t.me/", "").replace("@", "")
                for cl in self.workers:
                    try:
                        if "+" in text: await cl(functions.messages.ImportChatInviteRequest(hash=text.split('/')[-1]))
                        else: await cl(functions.channels.JoinChannelRequest(channel=clean))
                    except: pass
                await event.reply("✅ Task Finished.")
                del self.waiting_for[OWNER_ID]

            elif mode == "spam_data":
                try:
                    target, msg = text.split('|')
                    target = target.strip()
                    await event.reply(f"🚀 Launching Spam on `{target}`...")
                    for _ in range(5): # Default 5 loops
                        tasks = [cl.send_message(target, msg.strip()) for cl in self.workers]
                        await asyncio.gather(*tasks, return_exceptions=True)
                    await event.reply("✅ Spam Assault Completed.")
                except:
                    await event.reply("❌ Error! Use format: `@username | Message`")
                del self.waiting_for[OWNER_ID]

        await self.bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(ZexoV12().start())
    except KeyboardInterrupt:
        pass
