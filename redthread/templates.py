"""Fixed replies. They answer when a model is slow or down, and they carry every safety message.

Hindi and Tamil lines need a check by a native speaker before anyone relies on them.
"""
from redthread.amounts import format_rupees
from redthread.reply import Answer
from redthread.stages import SIGNALS

LANGUAGES = ("en", "hi", "ta")
MAX_QUOTE_SHOWN = 120

_TEXT = {
    "welcome": {
        "en": ("Red Thread is an AI guide, not the police, a bank or a government service.",
               "It is free. We never ask for an OTP, PAN, password or fee.",
               "Forward anything you are unsure about, or tell us what happened."),
        "hi": ("Red Thread एक AI गाइड है, पुलिस, बैंक या सरकारी सेवा नहीं।",
               "यह मुफ़्त है। हम कभी OTP, PAN, पासवर्ड या फ़ीस नहीं मांगते।",
               "जिस पर शक हो उसे फ़ॉरवर्ड करें, या बताएं कि क्या हुआ।"),
        "ta": ("Red Thread ஒரு AI வழிகாட்டி. இது காவல்துறை, வங்கி அல்லது அரசு சேவை அல்ல.",
               "இது இலவசம். OTP, PAN, கடவுச்சொல் அல்லது கட்டணம் ஒருபோதும் கேட்க மாட்டோம்.",
               "சந்தேகமானதை ஃபார்வர்டு செய்யுங்கள், அல்லது என்ன நடந்தது என்று சொல்லுங்கள்."),
    },
    "decline": {
        "en": ("We only help with scams and with knowing who to trust.", "",
               "Forward a message or chat you are unsure about. If it is urgent, call 1930."),
        "hi": ("हम सिर्फ़ स्कैम और भरोसे से जुड़े सवालों में मदद करते हैं।", "",
               "जिस मैसेज या चैट पर शक हो उसे फ़ॉरवर्ड करें। ज़रूरी हो तो 1930 पर कॉल करें।"),
        "ta": ("மோசடி மற்றும் யாரை நம்புவது என்பதில் மட்டுமே உதவுகிறோம்.", "",
               "சந்தேகமான செய்தியை ஃபார்வர்டு செய்யுங்கள். அவசரம் என்றால் 1930 அழையுங்கள்."),
    },
    "secret": {
        "en": ("Please do not send that here.", "We removed it and did not keep it. We never need your OTP, PAN, card or password.",
               "Carry on without it."),
        "hi": ("कृपया यह यहां न भेजें।", "हमने इसे हटा दिया और रखा नहीं। हमें आपका OTP, PAN, कार्ड या पासवर्ड कभी नहीं चाहिए।",
               "इसके बिना आगे बढ़ें।"),
        "ta": ("தயவுசெய்து இதை இங்கே அனுப்ப வேண்டாம்.", "அதை நீக்கிவிட்டோம், வைத்துக்கொள்ளவில்லை. உங்கள் OTP, PAN, கார்டு அல்லது கடவுச்சொல் எங்களுக்கு தேவையில்லை.",
               "அது இல்லாமல் தொடருங்கள்."),
    },
    "relay_recovery_offer": {
        "en": ("That message was not sent.", "Offers to recover money are a common second scam, so they never pass through here.",
               "Keep talking here without offers."),
        "hi": ("वह मैसेज नहीं भेजा गया।", "पैसे वापस दिलाने के ऑफ़र अक्सर दूसरा स्कैम होते हैं, इसलिए वे यहां से नहीं जाते।",
               "ऑफ़र के बिना यहां बात जारी रखें।"),
        "ta": ("அந்த செய்தி அனுப்பப்படவில்லை.", "பணத்தை மீட்டுத் தருவதாகச் சொல்வது பொதுவான இரண்டாவது மோசடி, அதனால் அவை இங்கே செல்லாது.",
               "அத்தகைய சலுகைகள் இல்லாமல் இங்கே பேசுங்கள்."),
    },
    "relay_contact_details": {
        "en": ("That message was not sent.", "Numbers, links and payment IDs stay out of this chat to keep you both safe.",
               "Keep talking here without contact details."),
        "hi": ("वह मैसेज नहीं भेजा गया।", "आप दोनों की सुरक्षा के लिए नंबर, लिंक और पेमेंट ID इस चैट में नहीं जाते।",
               "संपर्क जानकारी के बिना यहां बात जारी रखें।"),
        "ta": ("அந்த செய்தி அனுப்பப்படவில்லை.", "உங்கள் இருவரின் பாதுகாப்புக்காக எண்கள், இணைப்புகள், பணம் செலுத்தும் ID இங்கே செல்லாது.",
               "தொடர்பு விவரங்கள் இல்லாமல் இங்கே பேசுங்கள்."),
    },
    "relay_sensitive": {
        "en": ("That message was not sent.", "It held private details such as a code or ID number.", "Keep those to yourself."),
        "hi": ("वह मैसेज नहीं भेजा गया।", "उसमें कोड या ID नंबर जैसी निजी जानकारी थी।", "इन्हें अपने पास ही रखें।"),
        "ta": ("அந்த செய்தி அனுப்பப்படவில்லை.", "அதில் குறியீடு அல்லது ID எண் போன்ற தனிப்பட்ட விவரங்கள் இருந்தன.", "அவற்றை உங்களிடமே வைத்திருங்கள்."),
    },
    "call_fake_authority": {
        "en": ("Police and CBI do not arrest anyone over a call.", "No real officer asks you to stay on video or pay to clear your name.",
               "Hang up now. Do not pay. Then call 1930 yourself."),
        "hi": ("पुलिस या CBI कॉल पर किसी को गिरफ़्तार नहीं करती।", "कोई असली अफ़सर वीडियो पर रुकने या नाम साफ़ करने के लिए पैसे देने को नहीं कहता।",
               "अभी फ़ोन काट दें। पैसे न दें। फिर खुद 1930 पर कॉल करें।"),
        "ta": ("காவல்துறையோ CBI யோ அழைப்பில் யாரையும் கைது செய்வதில்லை.", "உண்மையான அதிகாரி வீடியோவில் இருக்கச் சொல்லவோ பணம் கேட்கவோ மாட்டார்.",
               "இப்போதே அழைப்பை துண்டியுங்கள். பணம் தராதீர்கள். பிறகு நீங்களே 1930 அழையுங்கள்."),
    },
    "call_code_request": {
        "en": ("Nobody real needs your code.", "Anyone asking for it on a call is trying to take your money.", "Hang up. Do not share the code."),
        "hi": ("किसी असली व्यक्ति को आपके कोड की ज़रूरत नहीं होती।", "कॉल पर इसे मांगने वाला आपके पैसे लेना चाहता है।", "फ़ोन काट दें। कोड न बताएं।"),
        "ta": ("உண்மையான யாருக்கும் உங்கள் குறியீடு தேவையில்லை.", "அழைப்பில் அதைக் கேட்பவர் உங்கள் பணத்தை எடுக்க முயல்கிறார்.", "அழைப்பை துண்டியுங்கள். குறியீட்டை சொல்லாதீர்கள்."),
    },
    "call_remote_app": {
        "en": ("Do not install it.", "That kind of app lets them control your phone.", "Hang up now. Do not open your bank app."),
        "hi": ("इसे इंस्टॉल न करें।", "ऐसा ऐप उन्हें आपका फ़ोन चलाने देता है।", "अभी फ़ोन काट दें। अपना बैंक ऐप न खोलें।"),
        "ta": ("அதை நிறுவ வேண்டாம்.", "அந்த செயலி உங்கள் தொலைபேசியை அவர்கள் இயக்க அனுமதிக்கும்.", "இப்போதே அழைப்பை துண்டியுங்கள். வங்கி செயலியை திறக்காதீர்கள்."),
    },
    "call_safe_account": {
        "en": ("There is no such thing as a safe account.", "Banks and police never ask you to move money to protect it.",
               "Hang up. Do not move money. Call your bank from the number on your card."),
        "hi": ("सेफ़ अकाउंट जैसी कोई चीज़ नहीं होती।", "बैंक या पुलिस पैसे बचाने के लिए उन्हें कहीं भेजने को नहीं कहते।",
               "फ़ोन काट दें। पैसे न भेजें। कार्ड पर लिखे नंबर से बैंक को कॉल करें।"),
        "ta": ("பாதுகாப்பான கணக்கு என்று ஒன்று இல்லை.", "வங்கியோ காவல்துறையோ பணத்தை மாற்றச் சொல்வதில்லை.",
               "அழைப்பை துண்டியுங்கள். பணத்தை மாற்றாதீர்கள். கார்டில் உள்ள எண்ணில் வங்கியை அழையுங்கள்."),
    },
    "loss": {
        "en": ("This happens to careful people.", "We have it: {amount}.", "First, call 1930 now and say the line above."),
        "hi": ("ऐसा समझदार लोगों के साथ भी होता है।", "हमने नोट किया: {amount}।", "सबसे पहले अभी 1930 पर कॉल करें और ऊपर वाली बात कहें।"),
        "ta": ("கவனமானவர்களுக்கும் இது நடக்கும்.", "குறித்துக்கொண்டோம்: {amount}.", "முதலில் இப்போதே 1930 அழைத்து மேலே உள்ளதைச் சொல்லுங்கள்."),
    },
    "call_script": {
        "en": ('Say: "I lost {amount} to an online scam.{reference} Please put a hold on it."', " The reference is {value}.", "money"),
        "hi": ('कहें: "मेरे {amount} ऑनलाइन ठगी में चले गए।{reference} कृपया इसे होल्ड कराएं।"', " रेफरेंस {value} है।", "पैसे"),
        "ta": ('சொல்லுங்கள்: "ஆன்லைன் மோசடியில் {amount} இழந்தேன்.{reference} தயவுசெய்து இதை நிறுத்தி வையுங்கள்."', " ரெஃபரன்ஸ் {value}.", "பணம்"),
    },
    "script_head": {
        "en": ("When 1930 answers, say this.", "Call 1930 now. Then tap below."),
        "hi": ("1930 पर बात हो तो यह कहें।", "अभी 1930 पर कॉल करें। फिर नीचे दबाएं।"),
        "ta": ("1930 அழைப்பு எடுத்ததும் இதைச் சொல்லுங்கள்.", "இப்போதே 1930 அழையுங்கள். பிறகு கீழே அழுத்துங்கள்."),
    },
    "loss_question": {
        "en": "Answer when you can, but call 1930 first. Time matters.",
        "hi": "जब हो सके जवाब दें, पर पहले 1930 पर कॉल करें। समय मायने रखता है।",
        "ta": "முடியும்போது பதில் சொல்லுங்கள், ஆனால் முதலில் 1930 அழையுங்கள். நேரம் முக்கியம்.",
    },
    "known_script": {
        "en": ("This follows a known scam script.", "Hold off on paying. You decide."),
        "hi": ("यह एक जाने-माने स्कैम के तरीके जैसा है।", "अभी पैसे न दें। फ़ैसला आपका है।"),
        "ta": ("இது தெரிந்த மோசடி முறையைப் போல் உள்ளது.", "இப்போதைக்கு பணம் தராதீர்கள். முடிவு உங்களுடையது."),
    },
    "early_signs": {
        "en": ("There are early signs worth watching.", "Do not send money or codes yet. You decide."),
        "hi": ("कुछ शुरुआती संकेत हैं जिन पर ध्यान देना चाहिए।", "अभी पैसे या कोड न भेजें। फ़ैसला आपका है।"),
        "ta": ("கவனிக்க வேண்டிய ஆரம்ப அறிகுறிகள் உள்ளன.", "இப்போது பணமோ குறியீடோ அனுப்பாதீர்கள். முடிவு உங்களுடையது."),
    },
    "nothing_found": {
        "en": ("We found no known scam script in what you sent.", "This does not prove anyone is safe.",
               "Be careful with anyone asking for money or codes."),
        "hi": ("आपने जो भेजा उसमें कोई जाना-माना स्कैम तरीका नहीं मिला।", "इससे यह साबित नहीं होता कि कोई सुरक्षित है।",
               "पैसे या कोड मांगने वाले से सावधान रहें।"),
        "ta": ("நீங்கள் அனுப்பியதில் தெரிந்த மோசடி முறை கிடைக்கவில்லை.", "இது யாரும் பாதுகாப்பானவர் என்று நிரூபிக்காது.",
               "பணம் அல்லது குறியீடு கேட்பவர்களிடம் கவனமாக இருங்கள்."),
    },
    "day": {"en": "Day", "hi": "दिन", "ta": "நாள்"},
    "message": {"en": "Message", "hi": "मैसेज", "ta": "செய்தி"},
}
_BUTTONS = {
    "quotes": {"en": "Show the quotes", "hi": "सबूत दिखाएं", "ta": "ஆதாரம் காட்டு"},
    "report": {"en": "Report it", "hi": "रिपोर्ट करें", "ta": "புகார் செய்"},
    "paid": {"en": "I already paid", "hi": "मैं पैसे दे चुका", "ta": "பணம் கொடுத்தேன்"},
    "script": {"en": "Read the script", "hi": "क्या कहना है", "ta": "என்ன சொல்வது"},
    "called": {"en": "I have called", "hi": "कॉल कर लिया", "ta": "அழைத்துவிட்டேன்"},
    "call": {"en": "Test on a call", "hi": "कॉल पर जांचें", "ta": "அழைப்பில் சோதி"},
}
_MIN_TURNS_FOR_CALL = 6


def _button(key, language):
    return _BUTTONS[key][language]


def button_key(label):
    """The language-free id behind a button. A person's name has no key, so it stands for itself."""
    return next((key for key, labels in _BUTTONS.items() if label in labels.values()), label)


def _three(key, language, buttons=()):
    headline, proof, next_step = _TEXT[key][language]
    return Answer(headline, [proof] if proof else [], next_step, list(buttons))


def welcome(language="en"):
    return _three("welcome", language)


def decline(language="en"):
    return _three("decline", language)


def secret_refused(kinds, language="en"):
    return _three("secret", language)


def relay_blocked(reason, language="en"):
    return _three(f"relay_{reason}", language)


def during_call(kind, language="en"):
    return _three(f"call_{kind}", language)


def call_script(amount, reference, language="en"):
    """The words to say when 1930 answers. Code fills in the amount and reference the person gave."""
    script, reference_part, no_amount = _TEXT["call_script"][language]
    said = script.format(amount=format_rupees(amount) if amount is not None else no_amount,
                         reference=reference_part.format(value=reference) if reference else "")
    headline, next_step = _TEXT["script_head"][language]
    return Answer(headline, [said], next_step, [_button("called", language)])


def after_loss(reconciled, language="en"):
    headline, known, next_step = _TEXT["loss"][language]
    buttons = [_button("script", language), _button("called", language)]
    if reconciled.questions:
        return Answer(headline, reconciled.questions[:1], _TEXT["loss_question"][language], buttons)
    proof = [known.format(amount=format_rupees(reconciled.amount))] if reconciled.amount is not None else []
    script, reference_part, no_amount = _TEXT["call_script"][language]
    amount = format_rupees(reconciled.amount) if reconciled.amount is not None else no_amount
    reference = reference_part.format(value=reconciled.reference) if reconciled.reference else ""
    proof.append(script.format(amount=amount, reference=reference))
    return Answer(headline, proof, next_step, buttons)


def _place(chat, turn_index, language):
    dated = [turn.when for turn in chat.turns if turn.when]
    if dated and chat.turns[turn_index].when:
        return f"{_TEXT['day'][language]} {(chat.turns[turn_index].when.date() - dated[0].date()).days + 1}"
    return f"{_TEXT['message'][language]} {turn_index + 1}"


def _pick_proof(findings):
    """One quote per sign, money signs first, then shown in chat order."""
    rank = {"sinker": 0, "line": 1, "hook": 2}
    first_per_signal = {}
    for found in findings:
        first_per_signal.setdefault(found.signal, found)
    chosen = sorted(first_per_signal.values(), key=lambda found: (rank[SIGNALS[found.signal][0]], found.turn))[:3]
    return sorted(chosen, key=lambda found: found.turn)


def _shorten(quote):
    return quote if len(quote) <= MAX_QUOTE_SHOWN else quote[:MAX_QUOTE_SHOWN].rstrip() + "..."


def script_report(verdict, findings, chat, language="en"):
    """When a chat is not clear either way, the next step is to test the story on a call. A known script gets reported."""
    can_call = [_button("call", language)] if len(chat.turns) >= _MIN_TURNS_FOR_CALL else []
    if verdict.level == "nothing_found":
        return _three("nothing_found", language, [*can_call, _button("paid", language)])
    headline, next_step = _TEXT[verdict.level][language]
    proof = [f'{_place(chat, found.turn, language)}: "{_shorten(found.quote)}"' for found in _pick_proof(findings)]
    if verdict.level == "known_script" or not can_call:
        return Answer(headline, proof, next_step, [_button("quotes", language), _button("report", language), _button("paid", language)])
    return Answer(headline, proof, next_step, [*can_call, _button("quotes", language), _button("paid", language)])
