"""What the assistant knows, as data.

Adding a topic means adding an Entry — no code changes. Each entry carries
several phrasings of the same question, because the retriever matches on
words and one phrasing is a thin target.

Two kinds of entry:

  · A plain answer, written here in full.
  · A GROUNDED intent, where `handler` names a function in answers.py that
    reads the asker's own records. Those never hardcode a number; they call
    the same functions the site uses, so the bot and the pages can never
    disagree about money.

`audience` limits who an entry can be shown to. 'student' answers are never
served to a tutor, and nothing marked needs_login reaches an anonymous
visitor — they get an invitation to log in instead.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entry:
    intent: str
    phrasings: tuple           # ways people ask it
    answer: str = ''           # a fixed answer, if it needs no live data
    handler: str = ''          # or a function in answers.py that builds one
    needs_login: bool = False
    audience: tuple = ('student', 'tutor', 'staff', 'anonymous')
    follow_ups: tuple = ()     # suggested next questions
    actions: tuple = ()        # (label, url) deep links offered with the answer


# ─────────────────────────────────────────────────────────────────────────
#  General — anyone may ask these
# ─────────────────────────────────────────────────────────────────────────
GENERAL = [
    Entry(
        intent='find_courses',
        phrasings=('are there any teachers', 'are there any available teachers',
                   'are there any tutors', 'is anyone available to teach',
                   'who teaches physics', 'do you have any maths tutors',
                   'any chemistry teachers', 'teachers for mathematics',
                   'i want to learn maths', 'i want to learn',
                   'show me available courses', 'what courses are available',
                   'show me maths courses', 'show me the courses',
                   'list the courses', 'see all courses',
                   'which tutors are free', 'anyone teaching english',
                   'shikkhok ache', 'teacher ache', 'ke porabe',
                   'available tutors right now', 'who can teach me'),
        handler='answer_find_courses',
        follow_ups=('How do I book a tutor?',
                    'How much does a tutor cost?',
                    'What subjects can I learn?'),
    ),
    Entry(
        intent='subjects_offered',
        phrasings=('what subjects can i learn', 'what subjects do you offer',
                   'what can i study here', 'list of subjects',
                   'which subjects are taught', 'what do you teach',
                   'kon kon subject ache', 'what is on offer'),
        handler='answer_subjects_offered',
        follow_ups=('How do I book a tutor?', 'How much does a tutor cost?'),
    ),
    Entry(
        intent='cheapest_course',
        phrasings=('what is the cheapest course', 'cheapest tutor',
                   'lowest price course', 'most affordable tutor',
                   'shobcheye kom dam', 'cheapest maths course',
                   'which course costs least'),
        handler='answer_cheapest',
        follow_ups=('How do I book a tutor?', 'Can I pay in instalments?'),
    ),
    Entry(
        intent='greeting',
        phrasings=('hello', 'hi', 'hey', 'good morning', 'good evening',
                   'assalamu alaikum', 'salam', 'nomoskar', 'kemon acho'),
        answer='Hello. I can help with finding a tutor, what things cost, '
               'payments, refunds and your own bookings. What would you like '
               'to know?',
        follow_ups=('How much does a tutor cost?',
                    'How do I book a tutor?',
                    'How do refunds work?'),
    ),
    Entry(
        intent='thanks',
        phrasings=('thanks', 'thank you', 'thanx', 'dhonnobad', 'shukriya',
                   'appreciated', 'cheers'),
        answer='Glad to help. Ask me anything else about your tutoring.',
    ),
    Entry(
        intent='who_are_you',
        phrasings=('who are you', 'are you a robot', 'are you human',
                   'are you a bot', 'what is this assistant', 'chatbot',
                   'help me', 'what can this assistant do',
                   'tell me about yourself', 'introduce yourself'),
        answer='I am the EduAI assistant. I know how this platform works — '
               'booking, pricing, payments, refunds and reviews — and if you '
               'are logged in I can look up your own bookings, what you owe '
               'and what a refund would come to.',
        follow_ups=('How much do I owe?',
                    'How does commission work?',
                    'How do I find a tutor?'),
    ),
    Entry(
        intent='how_to_find_tutor',
        phrasings=('how do i find a tutor', 'how to find a teacher',
                   'search for a tutor', 'browse tutors', 'looking for a tutor',
                   'find someone to teach me', 'tutor khujbo kivabe',
                   'where are the tutors', 'show me tutors'),
        answer='Open Find Tutors and search by subject or name. Each tutor '
               'lists what they teach, their monthly rate, the weekly slots '
               'they have free, and their rating from past students.',
        actions=(('Browse tutors', '/tutors/'),),
        follow_ups=('How much does a tutor cost?',
                    'How do I book a tutor?',
                    'How are tutors rated?'),
    ),
    Entry(
        intent='how_to_book',
        phrasings=('how do i book', 'how to book a tutor', 'booking process',
                   'how do i hire a tutor', 'how to start a course',
                   'booking kivabe korbo', 'how do i get started'),
        answer='Open a tutor\'s profile and choose Book. You pick how many '
               'months you want and, if they have published slots, which '
               'weekly slot. The booking goes to the tutor as a request — '
               'nothing is owed until they accept it.',
        actions=(('Browse tutors', '/tutors/'),),
        follow_ups=('When do I pay?',
                    'What if the tutor does not accept?',
                    'Can I cancel a booking?'),
    ),
    Entry(
        intent='pricing',
        phrasings=('how much does a tutor cost', 'what is the price',
                   'how expensive is it', 'what are the rates',
                   'tutor price', 'monthly rate', 'koto taka lagbe',
                   'how much money', 'what does it cost'),
        answer='Each tutor sets their own monthly rate — most are between '
               '৳3,000 and ৳6,000 a month. You are charged per month of '
               'tutoring, and the rate is fixed when you book, so a tutor '
               'raising their price later cannot change what you agreed.',
        actions=(('See tutors and their rates', '/tutors/'),),
        follow_ups=('Can I pay in instalments?',
                    'What payment methods can I use?',
                    'How does commission work?'),
    ),
    Entry(
        intent='payment_methods',
        phrasings=('what payment methods can i use', 'how do i pay',
                   'can i pay by card',
                   'do you take bkash', 'nagad rocket', 'payment options',
                   'kivabe payment korbo', 'online payment'),
        answer='You can pay by bKash, Nagad or Rocket by sending the money '
               'and submitting the transaction ID for an admin to check, or '
               'pay by card online through SSLCommerz, which confirms '
               'automatically.',
        follow_ups=('Can I pay in instalments?',
                    'How long does approval take?',
                    'Has my payment been approved?'),
    ),
    Entry(
        intent='instalments',
        phrasings=('can i pay in instalments', 'pay in parts', 'partial payment',
                   'pay monthly', 'do i pay all at once', 'split the payment',
                   'ek sathe dite hobe'),
        answer='No, you do not have to pay it all at once. A booking can be '
               'paid in as many instalments as you like — pay a month at a '
               'time, or any amount up to what is still owed.',
        follow_ups=('How much do I owe?',
                    'What payment methods can I use?'),
    ),
    Entry(
        intent='when_to_pay',
        phrasings=('when do i pay', 'when should i pay', 'do i pay upfront',
                   'pay before or after', 'kokhon payment'),
        answer='Only after the tutor accepts your booking. Until then nothing '
               'is owed and you can cancel free of charge — we will not take '
               'money for a request a tutor might still decline.',
        follow_ups=('How much do I owe?', 'Can I cancel a booking?'),
    ),
    Entry(
        intent='commission_general',
        phrasings=('how does commission work', 'what is the commission',
                   'what cut do you take', 'platform fee', 'service charge',
                   'how does eduai make money', 'brokerage'),
        answer='EduAI takes a commission from the tutor\'s side, not on top of '
               'your fee. What you pay is the tutor\'s advertised monthly rate '
               'and nothing more — there is no booking fee or surcharge.',
        audience=('student', 'anonymous'),
        follow_ups=('How much does a tutor cost?', 'Can I pay in instalments?'),
    ),
    Entry(
        intent='refund_policy',
        phrasings=('how do refunds work', 'what is the refund policy',
                   'can i get my money back', 'refund rules',
                   'taka ferot pabo', 'money back policy', 'refund percentage'),
        answer='If you end a booking early, what comes back depends on how far '
               'into the course you are, counted from the start date:\n'
               '• before it starts — everything\n'
               '• within the first week — 75%\n'
               '• within the second week — 50%\n'
               '• weeks three and four — 25%\n'
               '• after four weeks — nothing\n'
               'It applies to what you have actually paid. If your tutor ends '
               'the booking instead, you get back everything they did not '
               'teach, whatever week it is.',
        follow_ups=('What refund would I get if I stop now?',
                    'How do I end a booking?',
                    'How do I get the refund money?'),
    ),
    Entry(
        intent='how_to_terminate',
        phrasings=('how do i cancel my booking', 'how to stop a course',
                   'i want to quit', 'end my booking', 'withdraw from course',
                   'booking bondho korbo', 'i want to leave'),
        answer='Open the booking and choose End this booking. You give a '
               'reason, an admin checks it, and if a refund is due the form '
               'shows you the exact figure before you commit. If nothing has '
               'been paid yet you can simply cancel outright.',
        follow_ups=('What refund would I get if I stop now?',
                    'How do refunds work?',
                    'How long does approval take?'),
    ),
    Entry(
        intent='refund_payout',
        phrasings=('how will i receive the refund money', 'where does refund money go',
                   'how will i receive my refund', 'refund payment method',
                   'kivabe taka ferot pabo'),
        answer='When you request termination you say where the money should '
               'go — a bKash, Nagad or Rocket number, a bank account, or you '
               'can collect it from the office in person. Once an admin '
               'approves it, the refund is sent that way.',
        follow_ups=('How do refunds work?', 'How do I end a booking?'),
    ),
    Entry(
        intent='early_completion',
        phrasings=('can i finish the course early', 'complete before time',
                   'finish early', 'course shesh kore dite pari',
                   'mark as completed early', 'we finished ahead of schedule'),
        answer='Yes. Either you or your tutor can ask to finish early — open '
               'the booking, choose Request early completion and give a '
               'reason. An admin checks it, and once approved the course is '
               'complete and you can leave a review.',
        follow_ups=('How do I write a review?', 'When does my course end?'),
    ),
    Entry(
        intent='how_to_review',
        phrasings=('how do i leave a review', 'can i rate my tutor',
                   'write a review', 'give feedback', 'rating dibo kivabe'),
        answer='Once a course is complete, open the booking and choose Write '
               'a review. You give a rating out of five and a comment. It is '
               'public on the tutor\'s profile, and it is what their rating is '
               'built from.',
        follow_ups=('When does my course end?', 'How are tutors rated?'),
    ),
    Entry(
        intent='tutor_rating',
        phrasings=('how are tutors rated', 'what does the rating mean',
                   'is the rating reliable', 'how do you calculate rating'),
        answer='A tutor\'s rating is the average of every review left by '
               'students who actually completed a course with them. Nobody '
               'can review a course they did not take, and each booking can '
               'be reviewed only once.',
        follow_ups=('How do I find a tutor?', 'How do I leave a review?'),
    ),
    Entry(
        intent='become_tutor',
        phrasings=('how do i become a tutor', 'i want to teach',
                   'can i be a teacher', 'sign up as tutor', 'teaching job',
                   'shikkhok hote chai', 'how to join as a tutor'),
        answer='Register and choose Tutor as your role — you will need your '
               'NID number. After that, create your tutor profile with the '
               'subject you teach, a short bio and your monthly rate, and '
               'publish the weekly slots you are free. Students can find and '
               'book you as soon as your profile exists.',
        actions=(('Register', '/accounts/register/'),),
        follow_ups=('How much can I earn?', 'How does commission work?'),
    ),
    Entry(
        intent='tutor_commission',
        phrasings=('how much commission do you take', 'what is your cut',
                   'commission rate for tutors', 'what percentage do you take',
                   'how is tutor commission calculated'),
        answer='EduAI takes 50% of the first month of a booking, then 10% of '
               'every month after that. So on a ৳5,000 monthly rate over three '
               'months you receive ৳2,500 for the first month and ৳4,500 for '
               'each month after — ৳11,500 of the ৳15,000.',
        audience=('tutor', 'staff'),
        follow_ups=('How much have I earned?', 'When do I get paid?'),
    ),
    Entry(
        intent='signup',
        phrasings=('how do i register', 'create an account', 'sign up',
                   'make an account', 'account khulbo kivabe', 'join eduai'),
        answer='Choose Join free, pick whether you are a student or a tutor, '
               'and fill in your details. Tutors also need an NID number. It '
               'takes a minute and costs nothing.',
        actions=(('Register', '/accounts/register/'),),
        follow_ups=('How do I find a tutor?', 'How do I become a tutor?'),
    ),
    Entry(
        intent='approval_time',
        phrasings=('how long does approval take', 'how long to confirm',
                   'approval waiting time', 'koto somoy lagbe',
                   'how long until confirmed', 'delay in approval'),
        answer='A card payment through SSLCommerz confirms straight away. A '
               'bKash, Nagad or Rocket payment is checked by an admin against '
               'the transaction ID, which usually happens within a day.',
        follow_ups=('Has my payment been approved?', 'What payment methods can I use?'),
    ),
    Entry(
        intent='contact',
        phrasings=('how do i contact support', 'talk to a human', 'phone number',
                   'customer service', 'complain', 'contact you'),
        answer='For anything I cannot settle, an admin reviews payments and '
               'requests through the portal, and you can visit the office in '
               'person for refunds collected by hand.',
    ),
]

# ─────────────────────────────────────────────────────────────────────────
#  Grounded — answered from the asker's own records
# ─────────────────────────────────────────────────────────────────────────
GROUNDED = [
    Entry(
        intent='my_balance',
        phrasings=('how much do i owe', 'what is my balance', 'outstanding amount',
                   'how much is left to pay', 'do i owe anything', 'amar baki koto',
                   'how much have i paid', 'my dues'),
        handler='answer_balance',
        needs_login=True,
        audience=('student',),
        follow_ups=('What payment methods can I use?',
                    'Can I pay in instalments?',
                    'When does my course end?'),
    ),
    Entry(
        intent='my_refund_quote',
        phrasings=('what would my refund be', 'what refund would i get',
                   'how much would i get back',
                   'if i stop now what happens', 'refund if i quit today',
                   'ekhon bondho korle koto pabo', 'my refund amount',
                   'what if i cancel now'),
        handler='answer_refund_quote',
        needs_login=True,
        audience=('student',),
        follow_ups=('How do I end a booking?',
                    'How do refunds work?',
                    'How do I get the refund money?'),
    ),
    Entry(
        intent='my_bookings',
        phrasings=('what are my bookings', 'show my courses', 'my classes',
                   'which tutors do i have', 'amar booking', 'my current course',
                   'do i have any bookings', 'how many bookings do i have',
                   'count my bookings', 'list my bookings',
                   'how many courses am i taking'),
        handler='answer_bookings',
        needs_login=True,
        audience=('student', 'tutor', 'staff'),
        follow_ups=('How much do I owe?', 'When does my course end?'),
    ),
    Entry(
        intent='my_course_end',
        phrasings=('when does my course end', 'my course end date',
                   'how long is left on my course', 'when does my course finish',
                   'kobe shesh hobe', 'how many months left', 'course end date',
                   'time remaining on my course'),
        handler='answer_course_end',
        needs_login=True,
        audience=('student', 'tutor', 'staff'),
        follow_ups=('Can I finish the course early?', 'How do I leave a review?'),
    ),
    Entry(
        intent='my_payment_status',
        phrasings=('has my payment been approved', 'payment status',
                   'is my payment confirmed', 'did you get my payment',
                   'amar payment approve hoyeche', 'check my transaction',
                   'when will my payment be approved', 'my payment approved yet'),
        handler='answer_payment_status',
        needs_login=True,
        audience=('student',),
        follow_ups=('How long does approval take?', 'How much do I owe?'),
    ),
    Entry(
        intent='my_earnings',
        phrasings=('how much have i earned', 'my income', 'my earnings',
                   'what am i owed', 'how much do i earn', 'my total earnings',
                   'what have i been paid', 'amar income koto'),
        handler='answer_earnings',
        needs_login=True,
        audience=('tutor',),
        follow_ups=('How much commission do you take?', 'What are my bookings?'),
    ),
    Entry(
        intent='my_requests',
        phrasings=('what needs my attention', 'my pending requests',
                   'do i have any requests', 'anything waiting for me',
                   'my queue', 'my outstanding work'),
        handler='answer_pending_work',
        needs_login=True,
        audience=('tutor', 'staff'),
    ),
    Entry(
        intent='admin_queue',
        phrasings=('how many payments are waiting', 'what is in the admin queue',
                   'pending approvals', 'how much is owed in refunds',
                   'admin summary', 'what needs reviewing', 'my admin queue',
                   'what should i review', 'outstanding admin work'),
        handler='answer_admin_queue',
        needs_login=True,
        audience=('staff',),
    ),
]

ALL_ENTRIES = GENERAL + GROUNDED

# Every word the knowledge base knows, used to rescue typos.
VOCABULARY = set()
for _entry in ALL_ENTRIES:
    for _phrase in _entry.phrasings:
        from chatbot.normalise import normalise as _normalise
        VOCABULARY.update(_normalise(_phrase))


def entries_for(role, authenticated):
    """The entries this person may be matched against.

    A visitor who is not logged in can still MATCH a personal question — they
    are then invited to log in rather than told it was not understood, which
    is the difference between a helpful assistant and a wall. The engine
    enforces `needs_login`; this only decides what is visible to matching.
    """
    if not authenticated:
        return [e for e in ALL_ENTRIES
                if 'anonymous' in e.audience or e.needs_login]
    return [e for e in ALL_ENTRIES if role in e.audience]


def find(intent):
    return next((e for e in ALL_ENTRIES if e.intent == intent), None)
