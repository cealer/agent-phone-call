// Inbox screens: the list of notices and one notice in full.
import 'package:flutter/material.dart';

import 'notice_inbox.dart';
import 'ui.dart';

String categoryLabel(String category) => switch (category) {
  'meeting' => 'Meeting',
  'email' => 'Email',
  'decision' => 'Decision',
  'health' => 'Health',
  _ => 'Summary',
};

IconData categoryIcon(String category) => switch (category) {
  'meeting' => Icons.event_rounded,
  'email' => Icons.mail_outline_rounded,
  'decision' => Icons.help_outline_rounded,
  'health' => Icons.favorite_border_rounded,
  _ => Icons.wb_sunny_outlined,
};

/// «today 08:30», «yesterday 19:05» or «3 Oct 08:30».
String noticeTime(DateTime t, DateTime now) {
  String two(int n) => n.toString().padLeft(2, '0');
  final time = '${two(t.hour)}:${two(t.minute)}';
  final days = DateTime(
    now.year,
    now.month,
    now.day,
  ).difference(DateTime(t.year, t.month, t.day)).inDays;
  if (days == 0) return 'today $time';
  if (days == 1) return 'yesterday $time';
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  return '${t.day} ${months[t.month - 1]} $time';
}

/// Home screen row that leads to the inbox.
class NoticesEntry extends StatelessWidget {
  const NoticesEntry({super.key, required this.unread, required this.onTap});
  final int unread;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GroupCard(
    children: [
      InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            children: [
              const Icon(
                Icons.notifications_none_rounded,
                color: Palette.accent,
                size: 22,
              ),
              const SizedBox(width: 14),
              const Expanded(
                child: Text(
                  'Notices',
                  style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w500),
                ),
              ),
              if (unread > 0)
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 9,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: Palette.accent,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    '$unread',
                    style: const TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              const SizedBox(width: 6),
              const Icon(Icons.chevron_right_rounded, color: Palette.textDim),
            ],
          ),
        ),
      ),
    ],
  );
}

class NoticesScreen extends StatefulWidget {
  const NoticesScreen({super.key});

  @override
  State<NoticesScreen> createState() => _NoticesScreenState();
}

class _NoticesScreenState extends State<NoticesScreen>
    with WidgetsBindingObserver {
  List<StoredNotice>? _notices;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  Future<void> _load() async {
    final notices = await readInbox();
    if (mounted) setState(() => _notices = notices);
  }

  Future<void> _open(StoredNotice n) async {
    await Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => NoticeScreen(id: n.notice.id)));
    _load();
  }

  @override
  Widget build(BuildContext context) =>
      NoticesList(notices: _notices, now: DateTime.now(), onOpen: _open, onRefresh: _load);
}

/// The inbox list itself (kept apart from storage so it can be previewed and tested).
class NoticesList extends StatelessWidget {
  const NoticesList({
    super.key,
    required this.notices,
    required this.now,
    required this.onOpen,
    required this.onRefresh,
  });
  final List<StoredNotice>? notices;
  final DateTime now;
  final void Function(StoredNotice) onOpen;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final notices = this.notices;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Notices'),
        backgroundColor: Palette.background,
      ),
      body: notices == null
          ? const SizedBox.shrink()
          : notices.isEmpty
          ? const Center(
              child: Text(
                'No notices yet.',
                style: TextStyle(color: Palette.textDim),
              ),
            )
          : RefreshIndicator(
              color: Palette.accent,
              backgroundColor: Palette.surfaceHigh,
              onRefresh: onRefresh,
              child: ListView.separated(
                padding: const EdgeInsets.fromLTRB(18, 8, 18, 32),
                itemCount: notices.length,
                separatorBuilder: (_, _) => const SizedBox(height: 10),
                itemBuilder: (_, i) => _NoticeRow(
                  stored: notices[i],
                  now: now,
                  onTap: () => onOpen(notices[i]),
                ),
              ),
            ),
    );
  }
}

class _NoticeRow extends StatelessWidget {
  const _NoticeRow({
    required this.stored,
    required this.now,
    required this.onTap,
  });
  final StoredNotice stored;
  final DateTime now;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final notice = stored.notice;
    final unread = !stored.read;
    return Material(
      color: Palette.surface,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: Palette.outline.withValues(alpha: 0.6)),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                categoryIcon(notice.category),
                color: notice.urgent ? Palette.orange : Palette.textDim,
                size: 22,
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${categoryLabel(notice.category)} · ${noticeTime(stored.received, now)}',
                      style: const TextStyle(
                        color: Palette.textDim,
                        fontSize: 12.5,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      notice.title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 15.5,
                        fontWeight: unread ? FontWeight.w700 : FontWeight.w500,
                      ),
                    ),
                    if (notice.body.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(
                        notice.body,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: Palette.textDim,
                          fontSize: 13,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (unread) ...[
                const SizedBox(width: 10),
                Container(
                  key: const ValueKey('unread'),
                  margin: const EdgeInsets.only(top: 6),
                  width: 10,
                  height: 10,
                  decoration: const BoxDecoration(
                    color: Palette.accent,
                    shape: BoxShape.circle,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// One notice in full; opening it marks it as read.
class NoticeScreen extends StatefulWidget {
  const NoticeScreen({super.key, required this.id});
  final String id;

  @override
  State<NoticeScreen> createState() => _NoticeScreenState();
}

class _NoticeScreenState extends State<NoticeScreen> {
  StoredNotice? _notice;
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final notice = (await readInbox())
        .where((n) => n.notice.id == widget.id)
        .firstOrNull;
    if (notice != null) await markRead(widget.id);
    if (!mounted) return;
    setState(() {
      _notice = notice;
      _loaded = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    final stored = _notice;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Notice'),
        backgroundColor: Palette.background,
      ),
      body: !_loaded
          ? const SizedBox.shrink()
          : stored == null
          ? const Center(
              child: Text(
                'This notice is no longer stored.',
                style: TextStyle(color: Palette.textDim),
              ),
            )
          : NoticeDetail(stored: stored),
    );
  }
}

/// Title, category, time and full body (selectable text) of a notice.
class NoticeDetail extends StatelessWidget {
  const NoticeDetail({super.key, required this.stored});
  final StoredNotice stored;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(22, 12, 22, 32),
        children: [
          Row(
            children: [
              Icon(
                categoryIcon(stored.notice.category),
                color: stored.notice.urgent ? Palette.orange : Palette.accent,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                '${categoryLabel(stored.notice.category).toUpperCase()}${stored.notice.urgent ? ' · URGENT' : ''}',
                style: const TextStyle(
                  color: Palette.textDim,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 1.2,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          SelectableText(
            stored.notice.title,
            style: const TextStyle(
              fontSize: 26,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.4,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            noticeTime(stored.received, DateTime.now()),
            style: const TextStyle(color: Palette.textDim, fontSize: 13.5),
          ),
          const SizedBox(height: 22),
          SelectableText(
            stored.notice.body,
            style: const TextStyle(fontSize: 17, height: 1.5),
          ),
        ],
      ),
    );
  }
}
