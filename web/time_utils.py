from datetime import datetime, timezone, timedelta

def to_bn_digits(num_str):
    bn_digits = {'0':'০','1':'১','2':'২','3':'৩','4':'৪','5':'৫','6':'৬','7':'৭','8':'৮','9':'৯'}
    return ''.join(bn_digits.get(c, c) for c in str(num_str))

def timeago_bn(dt_str):
    """
    Returns relative time in Bengali, e.g.
    'এইমাত্র', '১৫ মিনিট আগে', '২ ঘণ্টা আগে', 'গতকাল', '৩ দিন আগে'
    """
    if not dt_str:
        return ''
    try:
        # SQLite stores UTC 'YYYY-MM-DD HH:MM:SS'
        # Handle if microsecond exists or format varies
        dt_clean = str(dt_str).split('.')[0]
        dt = datetime.strptime(dt_clean, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
        
        if diff < 0:
            return 'এইমাত্র'
        elif diff < 60:
            return 'এইমাত্র'
        elif diff < 3600:
            mins = max(1, int(diff // 60))
            return f"{to_bn_digits(mins)} মিনিট আগে"
        elif diff < 86400:
            hours = int(diff // 3600)
            return f"{to_bn_digits(hours)} ঘণ্টা আগে"
        elif diff < 172800:
            return "গতকাল"
        else:
            days = int(diff // 86400)
            return f"{to_bn_digits(days)} দিন আগে"
    except Exception:
        return str(dt_str)

def format_datetime_bn(dt_str):
    """
    Returns formatted Bangladesh Standard Time (UTC+6) date & time in Bengali, e.g.
    'রবিবার, ৬ সেপ্টেম্বর ২০২৬, ১৮:০৫'
    """
    if not dt_str:
        return ''
    try:
        dt_clean = str(dt_str).split('.')[0]
        dt = datetime.strptime(dt_clean, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        # Convert UTC to BST (UTC+6)
        bst = dt + timedelta(hours=6)
        
        weekdays = ['সোমবার', 'মঙ্গলবার', 'বুধবার', 'বৃহস্পতিবার', 'শুক্রবার', 'শনিবার', 'রবিবার']
        months = [
            'জানুয়ারি', 'ফেব্রুয়ারি', 'মার্চ', 'এপ্রিল', 'মে', 'জুন',
            'জুলাই', 'আগস্ট', 'সেপ্টেম্বর', 'অক্টোবর', 'নভেম্বর', 'ডিসেম্বর'
        ]
        
        day_name = weekdays[bst.weekday()]
        day_num = to_bn_digits(bst.day)
        month_name = months[bst.month - 1]
        year = to_bn_digits(bst.year)
        time_str = to_bn_digits(bst.strftime('%H:%M'))
        
        return f"{day_name}, {day_num} {month_name} {year}, {time_str}"
    except Exception:
        return str(dt_str)

