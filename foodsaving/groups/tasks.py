import traceback

from django.db.models import Count
from huey import crontab
from huey.contrib.djhuey import db_periodic_task
from influxdb_metrics.loader import write_points

from foodsaving.groups import stats, emails
from foodsaving.groups.emails import prepare_user_inactive_in_group_email
from foodsaving.groups.models import Group
from foodsaving.utils import stats_utils

from django.utils import timezone
from foodsaving.groups.models import GroupMembership
from config import settings

from datetime import timedelta


@db_periodic_task(crontab(minute=0))  # every hour
def record_group_stats():
    stats_utils.periodic_task('group__record_group_stats')

    points = []

    for group in Group.objects.all():
        points.extend(stats.get_group_members_stats(group))
        points.extend(stats.get_group_stores_stats(group))

    write_points(points)


@db_periodic_task(crontab(hour=2, minute=0))  # 2am every day
def process_inactive_users():
    now = timezone.now()

    count_users_flagged_inactive = 0
    count_users_removed = 0

#    remove_threshold_date = now - timedelta(days=settings.NUMBER_OF_DAYS_UNTIL_REMOVED_FROM_GROUP)
#    for gm in GroupMembership.objects.filter(inactive_at__lte=remove_threshold_date, active=False):
#        email = prepare_user_removed_from_group_email(gm.user, gm.group)
#        email.send()
#        gm.delete()
#        count_users_removed += 1

    inactive_threshold_date = now - timedelta(days=settings.NUMBER_OF_DAYS_UNTIL_INACTIVE_IN_GROUP)
    for gm in GroupMembership.objects.filter(lastseen_at__lte=inactive_threshold_date, inactive_at=None):
        email = prepare_user_inactive_in_group_email(gm.user, gm.group)
        email.send()
        gm.inactive_at = now
        gm.save()
        count_users_flagged_inactive += 1

    stats_utils.periodic_task('group__process_inactive_users', {
        'count_users_flagged_inactive': count_users_flagged_inactive,
        'count_users_removed': count_users_removed
    })


@db_periodic_task(crontab(day_of_week=0, hour=8, minute=0))  # send 8am every Sunday
def send_summary_emails():
    email_count = 0
    recipient_count = 0

    groups = Group.objects.annotate(member_count=Count('members')).filter(member_count__gt=0)

    for group in groups:

        from_date, to_date = emails.calculate_group_summary_dates(group)

        if not group.sent_summary_up_to or group.sent_summary_up_to < to_date:

            email_recipient_count = 0

            for email in emails.prepare_group_summary_emails(group, from_date, to_date):
                try:
                    email.send()
                    email_recipient_count += len(email.to)
                except Exception:
                    traceback.print_exc()

            # we save this even if some of the email sending, no retries right now basically...
            group.sent_summary_up_to = to_date
            group.save()

            stats.group_summary_email(group, email_recipient_count)

            recipient_count += email_recipient_count
            email_count += 1

    stats_utils.periodic_task('group__send_summary_emails', {
        'recipient_count': recipient_count,
        'email_count': email_count,
    })

