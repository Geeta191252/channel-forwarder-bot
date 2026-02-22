# Multi-language translations for the Telegram bot
# All 33 supported languages

# User language preferences {user_id: "lang_code"}
user_languages = {}

TRANSLATIONS = {
    # ===== WELCOME / START =====
    "welcome": {
        "en": "👋 **Hello!**\n**Group Help** is **the most complete** Bot to help you **manage** your groups easily and **safely**!\n\n👉 **Add me in a Supergroup** and promote me as **Admin** to let me get in action!\n\n❓ **WHICH ARE THE COMMANDS?** ❓\nPress /help to see **all the commands** and how they work!",
        "hi": "👋 **नमस्ते!**\n**Group Help** आपके ग्रुप्स को आसानी से और **सुरक्षित** रूप से **प्रबंधित** करने में आपकी सहायता करने के लिए **सबसे पूर्ण** बॉट है!\n\n👉 **मुझे एक सुपरग्रुप में जोड़ें** और मुझे एक्शन में लाने के लिए **एडमिन** बनाएं!\n\n❓ **कौन - कौन से कमांड्स हैं?** ❓\n**सभी कमांड्स** और उनके काम को जानने के लिए /help दबाएं!",
        "ur": "👋 **ہیلو!**\n**Group Help** آپ کے گروپس کو آسانی سے اور **محفوظ** طریقے سے **منظم** کرنے میں مدد کرنے کے لیے **سب سے مکمل** بوٹ ہے!\n\n👉 **مجھے ایک سپر گروپ میں شامل کریں** اور مجھے ایکشن میں لانے کے لیے **ایڈمن** بنائیں!\n\n❓ **کون کون سے کمانڈز ہیں؟** ❓\n**تمام کمانڈز** اور ان کے کام جاننے کے لیے /help دبائیں!",
        "ar": "👋 **مرحباً!**\n**Group Help** هو **الأكثر اكتمالاً** بوت لمساعدتك في **إدارة** مجموعاتك بسهولة و**بأمان**!\n\n👉 **أضفني في مجموعة فائقة** وقم بترقيتي كـ**مسؤول** لأبدأ العمل!\n\n❓ **ما هي الأوامر؟** ❓\nاضغط /help لرؤية **جميع الأوامر** وكيف تعمل!",
        "es": "👋 **¡Hola!**\n**Group Help** es el Bot **más completo** para ayudarte a **administrar** tus grupos fácilmente y de forma **segura**!\n\n👉 **Añádeme a un Supergrupo** y promuéveme como **Admin** para que entre en acción!\n\n❓ **¿CUÁLES SON LOS COMANDOS?** ❓\nPresiona /help para ver **todos los comandos** y cómo funcionan!",
        "fr": "👋 **Bonjour !**\n**Group Help** est le Bot **le plus complet** pour vous aider à **gérer** vos groupes facilement et en toute **sécurité** !\n\n👉 **Ajoutez-moi dans un Supergroupe** et promouvez-moi en tant qu'**Admin** pour me mettre en action !\n\n❓ **QUELLES SONT LES COMMANDES ?** ❓\nAppuyez sur /help pour voir **toutes les commandes** et comment elles fonctionnent !",
        "de": "👋 **Hallo!**\n**Group Help** ist der **vollständigste** Bot, der dir hilft, deine Gruppen einfach und **sicher** zu **verwalten**!\n\n👉 **Füge mich einer Supergruppe hinzu** und befördere mich zum **Admin**, damit ich loslegen kann!\n\n❓ **WELCHE BEFEHLE GIBT ES?** ❓\nDrücke /help um **alle Befehle** und ihre Funktionen zu sehen!",
        "it": "👋 **Ciao!**\n**Group Help** è il Bot **più completo** per aiutarti a **gestire** i tuoi gruppi facilmente e in modo **sicuro**!\n\n👉 **Aggiungimi in un Supergruppo** e promuovimi come **Admin** per farmi entrare in azione!\n\n❓ **QUALI SONO I COMANDI?** ❓\nPremi /help per vedere **tutti i comandi** e come funzionano!",
        "pt": "👋 **Olá!**\n**Group Help** é o Bot **mais completo** para ajudá-lo a **gerenciar** seus grupos facilmente e com **segurança**!\n\n👉 **Adicione-me em um Supergrupo** e promova-me como **Admin** para me colocar em ação!\n\n❓ **QUAIS SÃO OS COMANDOS?** ❓\nPressione /help para ver **todos os comandos** e como funcionam!",
        "ru": "👋 **Привет!**\n**Group Help** — это **самый полный** бот, который поможет вам **управлять** группами легко и **безопасно**!\n\n👉 **Добавьте меня в Супергруппу** и назначьте **Админом**, чтобы я начал работать!\n\n❓ **КАКИЕ ЕСТЬ КОМАНДЫ?** ❓\nНажмите /help, чтобы увидеть **все команды** и как они работают!",
        "tr": "👋 **Merhaba!**\n**Group Help** gruplarınızı kolayca ve **güvenli** bir şekilde **yönetmenize** yardımcı olacak **en eksiksiz** Bot'tur!\n\n👉 **Beni bir Süper Gruba ekleyin** ve harekete geçmem için **Admin** yapın!\n\n❓ **KOMUTLAR NELERDİR?** ❓\n**Tüm komutları** ve nasıl çalıştıklarını görmek için /help'e basın!",
        "id": "👋 **Halo!**\n**Group Help** adalah Bot **paling lengkap** untuk membantu Anda **mengelola** grup dengan mudah dan **aman**!\n\n👉 **Tambahkan saya ke Supergroup** dan jadikan saya **Admin** agar saya bisa beraksi!\n\n❓ **APA SAJA PERINTAHNYA?** ❓\nTekan /help untuk melihat **semua perintah** dan cara kerjanya!",
        "zh": "👋 **你好！**\n**Group Help** 是**最完整的**机器人，帮助你轻松**安全**地**管理**你的群组！\n\n👉 **将我添加到超级群组**并提升我为**管理员**以让我开始工作！\n\n❓ **有哪些命令？** ❓\n按 /help 查看**所有命令**及其工作方式！",
        "zt": "👋 **你好！**\n**Group Help** 是**最完整的**機器人，幫助你輕鬆**安全**地**管理**你的群組！\n\n👉 **將我添加到超級群組**並提升我為**管理員**以讓我開始工作！\n\n❓ **有哪些命令？** ❓\n按 /help 查看**所有命令**及其工作方式！",
        "uk": "👋 **Привіт!**\n**Group Help** — це **найповніший** бот, який допоможе вам **керувати** групами легко та **безпечно**!\n\n👉 **Додайте мене в Супергрупу** та призначте **Адміном**, щоб я почав працювати!\n\n❓ **ЯКІ Є КОМАНДИ?** ❓\nНатисніть /help, щоб побачити **всі команди** та як вони працюють!",
        "kk": "👋 **Сәлем!**\n**Group Help** — топтарыңызды оңай және **қауіпсіз** басқаруға көмектесетін **ең толық** бот!\n\n👉 **Мені Супергрупқа қосыңыз** және іске қосу үшін **Админ** етіп тағайындаңыз!\n\n❓ **ҚАНДАЙ КОМАНДАЛАР БАР?** ❓\n**Барлық командаларды** көру үшін /help басыңыз!",
        "uz": "👋 **Salom!**\n**Group Help** guruhlaringizni oson va **xavfsiz** boshqarishga yordam beradigan **eng toʻliq** botdir!\n\n👉 **Meni Superguruhga qoʻshing** va ishga tushirishim uchun **Admin** qiling!\n\n❓ **QANDAY BUYRUQLAR BOR?** ❓\n**Barcha buyruqlarni** koʻrish uchun /help bosing!",
        "uzc": "👋 **Салом!**\n**Group Help** гуруҳларингизни осон ва **хавфсиз** бошқаришга ёрдам берадиган **энг тўлиқ** ботдир!\n\n👉 **Мени Супергуруҳга қўшинг** ва ишга тушишим учун **Админ** қилинг!\n\n❓ **ҚАНДАЙ БУЙРУҚЛАР БОР?** ❓\n**Барча буйруқларни** кўриш учун /help босинг!",
        "az": "👋 **Salam!**\n**Group Help** qruplarınızı asanlıqla və **təhlükəsiz** idarə etməyə kömək edən **ən tam** Botdur!\n\n👉 **Məni Superqrupa əlavə edin** və fəaliyyətə keçməyim üçün **Admin** edin!\n\n❓ **ƏMRLƏR HANSıLARDIR?** ❓\n**Bütün əmrləri** görmək üçün /help basın!",
        "ms": "👋 **Halo!**\n**Group Help** ialah Bot **paling lengkap** untuk membantu anda **mengurus** kumpulan anda dengan mudah dan **selamat**!\n\n👉 **Tambahkan saya ke Superkumpulan** dan naikkan saya sebagai **Admin** untuk bertindak!\n\n❓ **APAKAH PERINTAHNYA?** ❓\nTekan /help untuk melihat **semua perintah** dan cara ia berfungsi!",
        "so": "👋 **Salaan!**\n**Group Help** waa Bot-ka **ugu dhamaystiran** ee kaa caawiya inaad **maamusho** kooxahaaga si fudud oo **badbaado** leh!\n\n👉 **Igu dar Superkoox** oo i dhig **Admin** si aan u shaqeeyo!\n\n❓ **AMARKA WAXAY YIHIIN?** ❓\n/help riix si aad u aragto **dhammaan amarrada**!",
        "sq": "👋 **Përshëndetje!**\n**Group Help** është Boti **më i plotë** për t'ju ndihmuar të **menaxhoni** grupet tuaja lehtësisht dhe me **siguri**!\n\n👉 **Shtoni në një Supergrup** dhe promovoni si **Admin** për të vepruar!\n\n❓ **CILAT JANË KOMANDAT?** ❓\nShtypni /help për të parë **të gjitha komandat** dhe si funksionojnë!",
        "sr": "👋 **Здраво!**\n**Group Help** је **најкомплетнији** бот који вам помаже да **управљате** групама лако и **безбедно**!\n\n👉 **Додајте ме у Супергрупу** и поставите ме за **Админа** да почнем да радим!\n\n❓ **КОЈЕ СУ КОМАНДЕ?** ❓\nПритисните /help да видите **све команде** и како раде!",
        "am": "👋 **ሰላም!**\n**Group Help** ቡድኖችዎን በቀላሉ እና **በደህና** **ለማስተዳደር** የሚረዳ **በጣም ሙሉ** ቦት ነው!\n\n👉 **በ Supergroup ውስጥ ጨምሩኝ** እና እንድሠራ **Admin** አድርጉኝ!\n\n❓ **ትዕዛዞች ምንድናቸው?** ❓\n**ሁሉንም ትዕዛዞች** ለማየት /help ይጫኑ!",
        "el": "👋 **Γεια!**\n**Group Help** είναι το **πιο ολοκληρωμένο** Bot για να σας βοηθήσει να **διαχειριστείτε** τις ομάδες σας εύκολα και **ασφαλώς**!\n\n👉 **Προσθέστε με σε ένα Supergroup** και προαγάγετέ με σε **Admin** για να αρχίσω!\n\n❓ **ΠΟΙΕΣ ΕΙΝΑΙ ΟΙ ΕΝΤΟΛΕΣ;** ❓\nΠατήστε /help για να δείτε **όλες τις εντολές**!",
        "ko": "👋 **안녕하세요!**\n**Group Help**는 그룹을 쉽고 **안전하게** **관리**할 수 있도록 도와주는 **가장 완벽한** 봇입니다!\n\n👉 **슈퍼그룹에 추가**하고 **관리자**로 승격시켜 주세요!\n\n❓ **명령어는 무엇인가요?** ❓\n/help를 눌러 **모든 명령어**를 확인하세요!",
        "fa": "👋 **سلام!**\n**Group Help** **کامل‌ترین** ربات برای کمک به **مدیریت** گروه‌های شما به راحتی و **امنیت** است!\n\n👉 **مرا به یک سوپرگروپ اضافه کنید** و برای شروع کار **ادمین** کنید!\n\n❓ **دستورات چیست؟** ❓\nبرای دیدن **همه دستورات** /help را فشار دهید!",
        "ku": "👋 **سڵاو!**\n**Group Help** **تەواوترین** بۆتە بۆ یارمەتیدانت بۆ **بەڕێوەبردنی** گروپەکانت بە ئاسانی و **بە ئارامی**!\n\n👉 **زیادم بکە بۆ سوپەرگروپ** و **ئەدمین**م بکە بۆ دەستپێکردن!\n\n❓ **فرمانەکان چین؟** ❓\n/help دابگرە بۆ بینینی **هەموو فرمانەکان**!",
        "si": "👋 **ආයුබෝවන්!**\n**Group Help** ඔබේ සමූහ පහසුවෙන් සහ **ආරක්ෂිතව** **කළමනාකරණය** කිරීමට උපකාර කරන **සම්පූර්ණම** බොට් එකයි!\n\n👉 **මාව Supergroup එකකට එක් කරන්න** සහ **Admin** ලෙස ප්‍රවර්ධනය කරන්න!\n\n❓ **විධාන මොනවාද?** ❓\n**සියලුම විධාන** බැලීමට /help ඔබන්න!",
        "bn": "👋 **হ্যালো!**\n**Group Help** আপনার গ্রুপগুলি সহজে এবং **নিরাপদে** **পরিচালনা** করতে সাহায্য করার জন্য **সবচেয়ে সম্পূর্ণ** বট!\n\n👉 **আমাকে একটি সুপারগ্রুপে যোগ করুন** এবং কাজ শুরু করতে **অ্যাডমিন** করুন!\n\n❓ **কমান্ডগুলি কী কী?** ❓\n**সমস্ত কমান্ড** দেখতে /help চাপুন!",
        "he": "👋 **שלום!**\n**Group Help** הוא הבוט **השלם ביותר** שיעזור לך **לנהל** את הקבוצות שלך בקלות וב**בטחה**!\n\n👉 **הוסיפו אותי לסופרגרופ** וקדמו אותי ל**אדמין** כדי שאתחיל לפעול!\n\n❓ **מהן הפקודות?** ❓\nלחצו /help כדי לראות **את כל הפקודות**!",
        "ro": "👋 **Salut!**\n**Group Help** este Botul **cel mai complet** pentru a te ajuta să **gestionezi** grupurile tale ușor și în **siguranță**!\n\n👉 **Adaugă-mă într-un Supergrup** și promovează-mă ca **Admin** pentru a intra în acțiune!\n\n❓ **CARE SUNT COMENZILE?** ❓\nApasă /help pentru a vedea **toate comenzile** și cum funcționează!",
        "nl": "👋 **Hallo!**\n**Group Help** is de **meest complete** Bot om je te helpen je groepen gemakkelijk en **veilig** te **beheren**!\n\n👉 **Voeg me toe aan een Supergroep** en promoveer me tot **Admin** om me in actie te laten komen!\n\n❓ **WAT ZIJN DE COMMANDO'S?** ❓\nDruk op /help om **alle commando's** te zien en hoe ze werken!",
    },

    # ===== BUTTONS =====
    "btn_add_group": {
        "en": "➕ Add me to a Group ➕", "hi": "➕ मुझे एक ग्रुप में जोड़ें ➕", "ur": "➕ مجھے ایک گروپ میں شامل کریں ➕",
        "ar": "➕ أضفني إلى مجموعة ➕", "es": "➕ Añádeme a un Grupo ➕", "fr": "➕ Ajoutez-moi à un Groupe ➕",
        "de": "➕ Füge mich einer Gruppe hinzu ➕", "it": "➕ Aggiungimi a un Gruppo ➕", "pt": "➕ Adicione-me a um Grupo ➕",
        "ru": "➕ Добавьте меня в Группу ➕", "tr": "➕ Beni bir Gruba Ekle ➕", "id": "➕ Tambahkan saya ke Grup ➕",
        "zh": "➕ 将我添加到群组 ➕", "zt": "➕ 將我添加到群組 ➕", "uk": "➕ Додайте мене в Групу ➕",
        "kk": "➕ Мені Топқа қосыңыз ➕", "uz": "➕ Meni Guruhga qoʻshing ➕", "uzc": "➕ Мени Гуруҳга қўшинг ➕",
        "az": "➕ Məni Qrupa əlavə et ➕", "ms": "➕ Tambahkan saya ke Kumpulan ➕", "so": "➕ Igu dar Koox ➕",
        "sq": "➕ Shto në një Grup ➕", "sr": "➕ Додајте ме у Групу ➕", "am": "➕ ወደ ቡድን ጨምሩኝ ➕",
        "el": "➕ Προσθέστε με σε Ομάδα ➕", "ko": "➕ 그룹에 추가 ➕", "fa": "➕ مرا به گروه اضافه کنید ➕",
        "ku": "➕ زیادم بکە بۆ گروپ ➕", "si": "➕ මාව කණ්ඩායමට එක් කරන්න ➕", "bn": "➕ আমাকে গ্রুপে যোগ করুন ➕",
        "he": "➕ הוסיפו אותי לקבוצה ➕", "ro": "➕ Adaugă-mă într-un Grup ➕", "nl": "➕ Voeg me toe aan een Groep ➕",
    },
    "btn_settings": {
        "en": "⚙️ Manage Group Settings ✍️", "hi": "⚙️ ग्रुप सेटिंग्स ✍️", "ur": "⚙️ گروپ سیٹنگز ✍️",
        "ar": "⚙️ إعدادات المجموعة ✍️", "es": "⚙️ Configuración del Grupo ✍️", "fr": "⚙️ Paramètres du Groupe ✍️",
        "de": "⚙️ Gruppeneinstellungen ✍️", "it": "⚙️ Impostazioni Gruppo ✍️", "pt": "⚙️ Configurações do Grupo ✍️",
        "ru": "⚙️ Настройки группы ✍️", "tr": "⚙️ Grup Ayarları ✍️", "id": "⚙️ Pengaturan Grup ✍️",
        "zh": "⚙️ 管理群组设置 ✍️", "zt": "⚙️ 管理群組設置 ✍️", "uk": "⚙️ Налаштування групи ✍️",
        "kk": "⚙️ Топ параметрлері ✍️", "uz": "⚙️ Guruh sozlamalari ✍️", "uzc": "⚙️ Гуруҳ созламалари ✍️",
        "az": "⚙️ Qrup Parametrləri ✍️", "ms": "⚙️ Tetapan Kumpulan ✍️", "so": "⚙️ Dejinta Kooxda ✍️",
        "sq": "⚙️ Cilësimet e Grupit ✍️", "sr": "⚙️ Подешавања групе ✍️", "am": "⚙️ የቡድን ቅንብሮች ✍️",
        "el": "⚙️ Ρυθμίσεις Ομάδας ✍️", "ko": "⚙️ 그룹 설정 ✍️", "fa": "⚙️ تنظیمات گروه ✍️",
        "ku": "⚙️ ڕێکخستنی گروپ ✍️", "si": "⚙️ කණ්ඩායම් සැකසීම් ✍️", "bn": "⚙️ গ্রুপ সেটিংস ✍️",
        "he": "⚙️ הגדרות קבוצה ✍️", "ro": "⚙️ Setări Grup ✍️", "nl": "⚙️ Groepsinstellingen ✍️",
    },
    "btn_group": {
        "en": "👥 Group", "hi": "👥 ग्रुप", "ur": "👥 گروپ", "ar": "👥 مجموعة", "es": "👥 Grupo",
        "fr": "👥 Groupe", "de": "👥 Gruppe", "it": "👥 Gruppo", "pt": "👥 Grupo", "ru": "👥 Группа",
        "tr": "👥 Grup", "id": "👥 Grup", "zh": "👥 群组", "zt": "👥 群組", "uk": "👥 Група",
        "kk": "👥 Топ", "uz": "👥 Guruh", "uzc": "👥 Гуруҳ", "az": "👥 Qrup", "ms": "👥 Kumpulan",
        "so": "👥 Koox", "sq": "👥 Grupi", "sr": "👥 Група", "am": "👥 ቡድን", "el": "👥 Ομάδα",
        "ko": "👥 그룹", "fa": "👥 گروه", "ku": "👥 گروپ", "si": "👥 කණ්ඩායම", "bn": "👥 গ্রুপ",
        "he": "👥 קבוצה", "ro": "👥 Grup", "nl": "👥 Groep",
    },
    "btn_channel": {
        "en": "📢 Channel", "hi": "📢 चैनल", "ur": "📢 چینل", "ar": "📢 قناة", "es": "📢 Canal",
        "fr": "📢 Canal", "de": "📢 Kanal", "it": "📢 Canale", "pt": "📢 Canal", "ru": "📢 Канал",
        "tr": "📢 Kanal", "id": "📢 Kanal", "zh": "📢 频道", "zt": "📢 頻道", "uk": "📢 Канал",
        "kk": "📢 Канал", "uz": "📢 Kanal", "uzc": "📢 Канал", "az": "📢 Kanal", "ms": "📢 Saluran",
        "so": "📢 Kanaal", "sq": "📢 Kanali", "sr": "📢 Канал", "am": "📢 ቻናል", "el": "📢 Κανάλι",
        "ko": "📢 채널", "fa": "📢 کانال", "ku": "📢 کەناڵ", "si": "📢 නාලිකාව", "bn": "📢 চ্যানেল",
        "he": "📢 ערוץ", "ro": "📢 Canal", "nl": "📢 Kanaal",
    },
    "btn_support": {
        "en": "🆘 Support", "hi": "🆘 सहायता", "ur": "🆘 مدد", "ar": "🆘 الدعم", "es": "🆘 Soporte",
        "fr": "🆘 Support", "de": "🆘 Hilfe", "it": "🆘 Supporto", "pt": "🆘 Suporte", "ru": "🆘 Поддержка",
        "tr": "🆘 Destek", "id": "🆘 Dukungan", "zh": "🆘 支持", "zt": "🆘 支持", "uk": "🆘 Підтримка",
        "kk": "🆘 Қолдау", "uz": "🆘 Yordam", "uzc": "🆘 Ёрдам", "az": "🆘 Dəstək", "ms": "🆘 Sokongan",
        "so": "🆘 Taageero", "sq": "🆘 Ndihmë", "sr": "🆘 Подршка", "am": "🆘 ድጋፍ", "el": "🆘 Υποστήριξη",
        "ko": "🆘 지원", "fa": "🆘 پشتیبانی", "ku": "🆘 پاڵپشتی", "si": "🆘 සහාය", "bn": "🆘 সহায়তা",
        "he": "🆘 תמיכה", "ro": "🆘 Suport", "nl": "🆘 Ondersteuning",
    },
    "btn_info": {
        "en": "💬 Information", "hi": "💬 जानकारी", "ur": "💬 معلومات", "ar": "💬 معلومات", "es": "💬 Información",
        "fr": "💬 Informations", "de": "💬 Informationen", "it": "💬 Informazioni", "pt": "💬 Informações",
        "ru": "💬 Информация", "tr": "💬 Bilgi", "id": "💬 Informasi", "zh": "💬 信息", "zt": "💬 資訊",
        "uk": "💬 Інформація", "kk": "💬 Ақпарат", "uz": "💬 Maʼlumot", "uzc": "💬 Маълумот", "az": "💬 Məlumat",
        "ms": "💬 Maklumat", "so": "💬 Macluumaad", "sq": "💬 Informacion", "sr": "💬 Информације", "am": "💬 መረጃ",
        "el": "💬 Πληροφορίες", "ko": "💬 정보", "fa": "💬 اطلاعات", "ku": "💬 زانیاری", "si": "💬 තොරතුරු",
        "bn": "💬 তথ্য", "he": "💬 מידע", "ro": "💬 Informații", "nl": "💬 Informatie",
    },
    "btn_languages": {
        "en": "🇬🇧 Languages 🇬🇧", "hi": "🇮🇳 भाषाएं 🇮🇳", "ur": "🇵🇰 زبانیں 🇵🇰", "ar": "🇸🇦 اللغات 🇸🇦",
        "es": "🇪🇸 Idiomas 🇪🇸", "fr": "🇫🇷 Langues 🇫🇷", "de": "🇩🇪 Sprachen 🇩🇪", "it": "🇮🇹 Lingue 🇮🇹",
        "pt": "🇧🇷 Idiomas 🇧🇷", "ru": "🇷🇺 Языки 🇷🇺", "tr": "🇹🇷 Diller 🇹🇷", "id": "🇮🇩 Bahasa 🇮🇩",
        "zh": "🇨🇳 语言 🇨🇳", "zt": "🇨🇳 語言 🇨🇳", "uk": "🇺🇦 Мови 🇺🇦", "kk": "🇰🇿 Тілдер 🇰🇿",
        "uz": "🇺🇿 Tillar 🇺🇿", "uzc": "🇺🇿 Тиллар 🇺🇿", "az": "🇦🇿 Dillər 🇦🇿", "ms": "🇲🇾 Bahasa 🇲🇾",
        "so": "🇸🇴 Luqadaha 🇸🇴", "sq": "🇦🇱 Gjuhët 🇦🇱", "sr": "🇷🇸 Језици 🇷🇸", "am": "🇪🇹 ቋንቋዎች 🇪🇹",
        "el": "🇬🇷 Γλώσσες 🇬🇷", "ko": "🇰🇷 언어 🇰🇷", "fa": "🇮🇷 زبان‌ها 🇮🇷", "ku": "☀️ زمانەکان ☀️",
        "si": "🇱🇰 භාෂා 🇱🇰", "bn": "🇧🇩 ভাষা 🇧🇩", "he": "🇮🇱 שפות 🇮🇱", "ro": "🇷🇴 Limbi 🇷🇴",
        "nl": "🇳🇱 Talen 🇳🇱",
    },
    "btn_back": {
        "en": "🔙 Back", "hi": "🔙 पीछे जाएं", "ur": "🔙 واپس جائیں", "ar": "🔙 رجوع", "es": "🔙 Atrás",
        "fr": "🔙 Retour", "de": "🔙 Zurück", "it": "🔙 Indietro", "pt": "🔙 Voltar", "ru": "🔙 Назад",
        "tr": "🔙 Geri", "id": "🔙 Kembali", "zh": "🔙 返回", "zt": "🔙 返回", "uk": "🔙 Назад",
        "kk": "🔙 Артқа", "uz": "🔙 Orqaga", "uzc": "🔙 Орқага", "az": "🔙 Geri", "ms": "🔙 Kembali",
        "so": "🔙 Dib u noqo", "sq": "🔙 Mbrapa", "sr": "🔙 Назад", "am": "🔙 ተመለስ", "el": "🔙 Πίσω",
        "ko": "🔙 뒤로", "fa": "🔙 بازگشت", "ku": "🔙 گەڕانەوە", "si": "🔙 ආපසු", "bn": "🔙 পিছনে",
        "he": "🔙 חזרה", "ro": "🔙 Înapoi", "nl": "🔙 Terug",
    },

    # ===== LANGUAGE SELECTION =====
    "lang_selected": {
        "en": "OK, from now on I will speak in English 🇬🇧",
        "hi": "ठीक है, अब से में हिंदी में बोलूंगा 🇮🇳",
        "ur": "ٹھیک ہے، اب سے میں اردو میں بات کروں گا 🇵🇰",
        "ar": "حسناً، من الآن سأتحدث بالعربية 🇸🇦",
        "es": "De acuerdo, a partir de ahora hablaré en Español 🇪🇸",
        "fr": "D'accord, à partir de maintenant je parlerai en Français 🇫🇷",
        "de": "OK, ab jetzt werde ich auf Deutsch sprechen 🇩🇪",
        "it": "OK, da ora in poi parlerò in Italiano 🇮🇹",
        "pt": "OK, a partir de agora vou falar em Português 🇧🇷",
        "ru": "Хорошо, теперь я буду говорить по-русски 🇷🇺",
        "tr": "Tamam, bundan sonra Türkçe konuşacağım 🇹🇷",
        "id": "Baik, mulai sekarang saya akan berbicara dalam Bahasa Indonesia 🇮🇩",
        "zh": "好的，从现在起我将用中文说话 🇨🇳",
        "zt": "好的，從現在起我將用中文說話 🇨🇳",
        "uk": "Добре, тепер я буду говорити українською 🇺🇦",
        "kk": "Жарайды, енді мен қазақша сөйлеймін 🇰🇿",
        "uz": "Yaxshi, endi men oʻzbekcha gaplashaman 🇺🇿",
        "uzc": "Яхши, энди мен ўзбекча гаплашаман 🇺🇿",
        "az": "Yaxşı, bundan sonra Azərbaycanca danışacağam 🇦🇿",
        "ms": "Baik, mulai sekarang saya akan berbicara dalam Bahasa Melayu 🇲🇾",
        "so": "Waa yahay, hadda waan ku hadli doonaa Soomaali 🇸🇴",
        "sq": "Në rregull, tani e tutje do të flas në Shqip 🇦🇱",
        "sr": "У реду, од сада ћу говорити на Српском 🇷🇸",
        "am": "እሺ ከአሁን ጀምሮ በአማርኛ እናገራለሁ 🇪🇹",
        "el": "Εντάξει, από τώρα θα μιλάω στα Ελληνικά 🇬🇷",
        "ko": "알겠습니다, 이제부터 한국어로 말하겠습니다 🇰🇷",
        "fa": "باشه، از حالا به فارسی صحبت می‌کنم 🇮🇷",
        "ku": "باشە، لە ئێستاوە بە کوردی قسە دەکەم ☀️",
        "si": "හරි, දැන් සිට මම සිංහලෙන් කතා කරන්නම් 🇱🇰",
        "bn": "ঠিক আছে, এখন থেকে আমি বাংলায় কথা বলব 🇧🇩",
        "he": "בסדר, מעכשיו אני אדבר בעברית 🇮🇱",
        "ro": "OK, de acum voi vorbi în Română 🇷🇴",
        "nl": "OK, vanaf nu zal ik in het Nederlands spreken 🇳🇱",
    },

    # ===== CHOOSE LANGUAGE HEADER =====
    "choose_language": {
        "en": "🌍 <b>Choose your language</b>",
        "hi": "🌍 <b>अपनी भाषा चुनें</b>",
        "ur": "🌍 <b>اپنی زبان منتخب کریں</b>",
        "ar": "🌍 <b>اختر لغتك</b>",
        "es": "🌍 <b>Elige tu idioma</b>",
        "fr": "🌍 <b>Choisissez votre langue</b>",
        "de": "🌍 <b>Wähle deine Sprache</b>",
        "it": "🌍 <b>Scegli la tua lingua</b>",
        "pt": "🌍 <b>Escolha seu idioma</b>",
        "ru": "🌍 <b>Выберите язык</b>",
        "tr": "🌍 <b>Dilinizi seçin</b>",
        "id": "🌍 <b>Pilih bahasa Anda</b>",
    },

    # ===== SETTINGS =====
    "settings_title": {
        "en": "⚙️ **{title} — Settings**\n\nSelect an option below:",
        "hi": "⚙️ **{title} — सेटिंग्स**\n\nनीचे एक विकल्प चुनें:",
        "ur": "⚙️ **{title} — سیٹنگز**\n\nنیچے ایک آپشن منتخب کریں:",
        "ar": "⚙️ **{title} — الإعدادات**\n\nاختر خياراً أدناه:",
        "es": "⚙️ **{title} — Configuración**\n\nSelecciona una opción:",
        "fr": "⚙️ **{title} — Paramètres**\n\nSélectionnez une option:",
        "de": "⚙️ **{title} — Einstellungen**\n\nWähle eine Option:",
        "it": "⚙️ **{title} — Impostazioni**\n\nSeleziona un'opzione:",
        "pt": "⚙️ **{title} — Configurações**\n\nSelecione uma opção:",
        "ru": "⚙️ **{title} — Настройки**\n\nВыберите опцию:",
        "tr": "⚙️ **{title} — Ayarlar**\n\nBir seçenek seçin:",
        "id": "⚙️ **{title} — Pengaturan**\n\nPilih opsi di bawah:",
    },

    # ===== ADMIN ONLY =====
    "admin_only": {
        "en": "⛔ Only Admin can use this command!",
        "hi": "⛔ सिर्फ एडमिन ही यह कमांड इस्तेमाल कर सकता है!",
        "ur": "⛔ صرف ایڈمن ہی یہ کمانڈ استعمال کر سکتا ہے!",
        "ar": "⛔ فقط المسؤول يمكنه استخدام هذا الأمر!",
        "es": "⛔ ¡Solo el Admin puede usar este comando!",
        "fr": "⛔ Seul l'Admin peut utiliser cette commande !",
        "de": "⛔ Nur der Admin kann diesen Befehl verwenden!",
        "it": "⛔ Solo l'Admin può usare questo comando!",
        "pt": "⛔ Apenas o Admin pode usar este comando!",
        "ru": "⛔ Только Админ может использовать эту команду!",
        "tr": "⛔ Bu komutu sadece Admin kullanabilir!",
        "id": "⛔ Hanya Admin yang bisa menggunakan perintah ini!",
        "zh": "⛔ 只有管理员才能使用此命令！",
        "ko": "⛔ 관리자만 이 명령을 사용할 수 있습니다!",
        "fa": "⛔ فقط ادمین می‌تواند از این دستور استفاده کند!",
        "bn": "⛔ শুধুমাত্র অ্যাডমিন এই কমান্ড ব্যবহার করতে পারে!",
    },

    # ===== PRIVACY POLICY =====
    "privacy_policy": {
        "en": "📋 Privacy policy", "hi": "📋 गोपनीयता नीति", "ur": "📋 رازداری کی پالیسی",
        "ar": "📋 سياسة الخصوصية", "es": "📋 Política de privacidad", "fr": "📋 Politique de confidentialité",
        "de": "📋 Datenschutz", "it": "📋 Informativa sulla privacy", "pt": "📋 Política de privacidade",
        "ru": "📋 Политика конфиденциальности", "tr": "📋 Gizlilik politikası", "id": "📋 Kebijakan privasi",
    },
}


def get_user_lang(user_id):
    """Get the language code for a user, default to 'en'"""
    return user_languages.get(user_id, "en")


def t(user_id, key, **kwargs):
    """Get translated text for a user. Falls back to English if translation not available."""
    lang = get_user_lang(user_id)
    text_dict = TRANSLATIONS.get(key, {})
    text = text_dict.get(lang) or text_dict.get("en", key)
    if kwargs:
        text = text.format(**kwargs)
    return text
