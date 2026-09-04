from pathlib import Path

path = Path("frontend/src/app/Shell.tsx")
text = path.read_text()

anchor = 'import { NotificationClient } from "../api/notifications";\n'
if 'import { VerificationClient } from "../api/verification";' not in text:
    if anchor not in text:
        raise SystemExit("notification client import anchor not found")
    text = text.replace(anchor, anchor + 'import { VerificationClient } from "../api/verification";\n', 1)

text = text.replace(
    'import { OverviewPage, RunDetailPage, UnavailablePage } from "../pages/Pages";\n',
    'import { OverviewPage, UnavailablePage } from "../pages/Pages";\n',
)
text = text.replace(
    'import { ReferenceDetailPage, ReferencesPage } from "../pages/ReferencePages";\n',
    'import { ReferencesPage } from "../pages/ReferencePages";\n',
)
text = text.replace('import { TaskDetailPage } from "../pages/TaskDetailPage";\n', '')

page_anchor = 'import { UsagePage } from "../pages/UsagePage";\n'
page_block = page_anchor + (
    'import { VerificationDetailPage, VerificationPage } from "../pages/VerificationPage";\n'
    'import {\n'
    '  VerificationBoundReferenceDetailPage,\n'
    '  VerificationBoundRunDetailPage,\n'
    '  VerificationBoundTaskDetailPage,\n'
    '} from "../pages/VerificationBoundPages";\n'
)
if 'from "../pages/VerificationPage"' not in text:
    if page_anchor not in text:
        raise SystemExit("usage page import anchor not found")
    text = text.replace(page_anchor, page_block, 1)

client_anchor = '''  const notificationClient = useMemo(
    () => new NotificationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
'''
client_block = client_anchor + '''  const verificationClient = useMemo(
    () => new VerificationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
'''
if 'const verificationClient = useMemo(' not in text:
    if client_anchor not in text:
        raise SystemExit("notification client construction anchor not found")
    text = text.replace(client_anchor, client_block, 1)

match_anchor = '  const approvalMatch = matchPath("/approvals/:approvalId", path);\n'
if 'const verificationMatch = matchPath(' not in text:
    if match_anchor not in text:
        raise SystemExit("approval route match anchor not found")
    text = text.replace(
        match_anchor,
        match_anchor + '  const verificationMatch = matchPath("/verification/:verificationId", path);\n',
        1,
    )

old_task = '  } else if (taskMatch) content = <TaskDetailPage client={client} taskId={taskMatch.taskId} />;\n'
new_task = '''  } else if (taskMatch) {
    content = (
      <VerificationBoundTaskDetailPage
        client={client}
        verificationClient={verificationClient}
        taskId={taskMatch.taskId}
      />
    );
  }
'''
if old_task not in text and '<VerificationBoundTaskDetailPage' not in text:
    raise SystemExit("task detail route anchor not found")
text = text.replace(old_task, new_task, 1)

old_run = '  else if (runMatch) content = <RunDetailPage client={client} runId={runMatch.runId} />;\n'
new_run = '''  else if (runMatch) {
    content = (
      <VerificationBoundRunDetailPage
        client={client}
        verificationClient={verificationClient}
        runId={runMatch.runId}
      />
    );
  }
'''
if old_run not in text and '<VerificationBoundRunDetailPage' not in text:
    raise SystemExit("run detail route anchor not found")
text = text.replace(old_run, new_run, 1)

old_reference = '''  else if (referenceMatch) {
    content = (
      <ReferenceDetailPage
        client={client}
        collection={referenceMatch.collection}
        resourceId={referenceMatch.resourceId}
      />
    );
'''
new_reference = '''  else if (referenceMatch) {
    content = (
      <VerificationBoundReferenceDetailPage
        client={client}
        verificationClient={verificationClient}
        collection={referenceMatch.collection}
        resourceId={referenceMatch.resourceId}
      />
    );
'''
if old_reference not in text and '<VerificationBoundReferenceDetailPage' not in text:
    raise SystemExit("reference detail route anchor not found")
text = text.replace(old_reference, new_reference, 1)

approval_anchor = '  } else if (path === "/approvals") {\n'
verification_routes = '''  } else if (path === "/verification") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Verification"
        resource="verifications"
      >
        <VerificationPage client={verificationClient} />
      </ManifestResourcePage>
    );
  } else if (verificationMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Verification"
        resource="verifications"
      >
        <VerificationDetailPage
          client={verificationClient}
          verificationId={verificationMatch.verificationId}
        />
      </ManifestResourcePage>
    );
  } else if (path === "/approvals") {
'''
if '<VerificationPage client={verificationClient} />' not in text:
    if approval_anchor not in text:
        raise SystemExit("approval route insertion anchor not found")
    text = text.replace(approval_anchor, verification_routes, 1)

path.write_text(text)
