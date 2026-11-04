# Judging tour

Five minutes, no install. Open https://red-thread-production.up.railway.app/press

## 1. Try one press (1 minute)

Under "3. Try it", tap "Send a screenshot" and pick a fake video call screen, for example the one at `/static/press-arrest-video.jpg`. Within about 5 seconds:

- the first notice: "This is a scam. You can hang up."
- the family text the phone would send
- seconds later, the second notice: "Checked with official sources", citing pib.gov.in
- "Your case" opens at the top of the page

Then send `/static/press-pay-fresh.jpg`. A payment right after the scam press gets "Stop. Do not pay." with the time of the earlier press.

On the case: tap "Yes, I paid" to start the first-hour timer, type or say what happened, and watch the cyber crime form fill. Tap "Delete everything" when done.

## 2. Map to the criteria

| Criterion | Where to look |
|---|---|
| Impact | Digital arrest and romance scams in India, at the moment they happen. One press during the call, a text to family that breaks the isolation the scam depends on, then the 1930 words and the form, because the first hour decides whether money can be frozen. |
| Use of Meta AI tooling | Five hosted models, each with one job: Spark 1.3 reads screens in about 5 s at minimal effort; Wonder 1.0 checks the scam's claim on the web and only official citations survive; Voice Transcribe 1.0 hears the victim's story in Indian languages; Muse Image 1.0 makes the fake screens that attack our checks; SAM 3.1 finds QR codes on bills. See `redthread/press.py`, `claims.py`, `aftercall.py`, `lab.py`. |
| Demo | It works on a real phone: the side button, a notification during the call, a real text to family, a second notice with a source, a case page. |
| Technical depth | Models write words, code decides: warnings come from fixed rules and quotes checked word for word; the family text and 1930 words are written by code; any number a model adds that the person never said is dropped. Every model call has a fallback within 12 s. Screens are never saved, nothing lasts past 24 hours, reports are salted hashes. 794 tests, and a test lab whose fake screens are made by Muse Image. |

## 3. Honest limits

- The screen and bill labs are small and invented. One rule was added after the lab missed a screen.
- A plain Spark read matched our pipeline on a public romance benchmark, so we claim action, not better detection.
- The phone shortcut is set up by hand from the recipe on `/press`.
- Red Thread never claims a person is real or honest. It is a prototype, not the police or a bank.
