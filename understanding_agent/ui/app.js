/**
 * Understanding Agent — Web UI Layer
 * Vanilla JavaScript interacting with real repository and attempt endpoints.
 */

(function () {
  'use strict';

  // Application State
  const state = {
    commits: [],
    selectedCommitId: null,
    selectedCommitData: null,
    selectedAttemptId: null,
    selectedAttemptData: null,
    searchQuery: '',
    currentView: 'overview', // 'overview' | 'attempt'
    auditFilter: 'all', // 'all' | 'failed' | 'passed'
    lastStandardsReport: [],
  };

  // DOM Elements
  const el = {
    commitList: document.getElementById('commit-list'),
    commitsCount: document.getElementById('commits-count'),
    commitSearch: document.getElementById('commit-search'),
    refreshBtn: document.getElementById('refresh-btn'),
    crumbCommitTitle: document.getElementById('crumb-commit-title'),
    emptyState: document.getElementById('empty-state'),
    commitOverviewView: document.getElementById('commit-overview-view'),
    attemptDetailView: document.getElementById('attempt-detail-view'),

    // Commit Overview elements
    overviewStatusBadge: document.getElementById('overview-status-badge'),
    overviewBranchBadge: document.getElementById('overview-branch-badge'),
    overviewMsgTitle: document.getElementById('overview-message-title'),
    overviewMsgBody: document.getElementById('overview-message-body'),
    overviewHash: document.getElementById('overview-hash'),
    btnCopyHash: document.getElementById('btn-copy-hash'),
    overviewAuthor: document.getElementById('overview-author'),
    overviewTimestamp: document.getElementById('overview-timestamp'),
    filesCountBadge: document.getElementById('files-count-badge'),
    filesList: document.getElementById('files-list'),
    sumWhatChanged: document.getElementById('sum-what-changed'),
    sumImpact: document.getElementById('sum-impact'),
    sumWhy: document.getElementById('sum-why'),
    sumRisks: document.getElementById('sum-risks'),
    attemptsCountBadge: document.getElementById('attempts-count-badge'),
    attemptsGrid: document.getElementById('attempts-grid'),

    // Attempt Detail elements
    btnBackToCommit: document.getElementById('btn-back-to-commit'),
    btnPrevAttempt: document.getElementById('btn-prev-attempt'),
    btnNextAttempt: document.getElementById('btn-next-attempt'),
    attemptStepper: document.getElementById('attempt-stepper'),
    attemptTitle: document.getElementById('attempt-title'),
    attemptStatusBadge: document.getElementById('attempt-status-badge'),
    attemptScoreBadge: document.getElementById('attempt-score-badge'),
    attemptTimestamp: document.getElementById('attempt-timestamp'),
    chatThread: document.getElementById('chat-thread'),

    // Coding Standards Terminal Card elements
    btnFilterAll: document.getElementById('btn-filter-all'),
    btnFilterFailed: document.getElementById('btn-filter-failed'),
    btnFilterPassed: document.getElementById('btn-filter-passed'),
    filterCountAll: document.getElementById('filter-count-all'),
    filterCountFailed: document.getElementById('filter-count-failed'),
    filterCountPassed: document.getElementById('filter-count-passed'),
    btnCopyTerminal: document.getElementById('btn-copy-terminal'),
    btnCopyTerminalIcon: document.getElementById('btn-copy-terminal-icon'),
    termBadgeFailed: document.getElementById('term-badge-failed'),
    termBadgePassed: document.getElementById('term-badge-passed'),
    termComplianceBar: document.getElementById('term-compliance-bar'),
    termComplianceLabel: document.getElementById('term-compliance-label'),
    termSummaryCounts: document.getElementById('term-summary-counts'),
    attemptViolationsList: document.getElementById('attempt-violations-list'),
  };


  // Helpers


  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatDate(isoStr) {
    if (!isoStr) return '';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoStr;
    }
  }


  // API Calls


  async function fetchCommits(preserveSelection = false) {
    try {
      const res = await fetch('/api/commits');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const rawCommits = Array.isArray(data) ? data : [];
      // Only passed committed commits on the left side (no current staged, no failed)
      state.commits = rawCommits.filter((c) => {
        if (c.is_staged || c.id === 'staged') return false;
        const st = (c.status || '').toUpperCase();
        return st === 'PASSED';
      });

      renderSidebar();

      if (!state.selectedCommitId || !state.commits.some((c) => c.id === state.selectedCommitId)) {
        if (state.commits.length > 0) {
          selectCommit(state.commits[0].id);
        }
        return;
      }

      // If preserving selection (e.g. background polling or refresh):
      if (preserveSelection) {
        // If user is currently inspecting an attempt detail view, DO NOT interrupt them!
        if (state.currentView === 'attempt') {
          return;
        }

        // If user is on overview view, update the data silently in background
        const exists = state.commits.find((c) => c.id === state.selectedCommitId);
        if (exists) {
          const freshData = await fetchCommitDetails(state.selectedCommitId);
          if (freshData && state.currentView === 'overview') {
            state.selectedCommitData = freshData;
            renderCommitOverview(freshData);
          }
        }
        return;
      }

      // Explicit selection refresh
      if (state.commits.length > 0) {
        selectCommit(state.commits[0].id);
      }
    } catch (err) {
      console.error('Failed to load commits:', err);
      el.commitList.innerHTML = `
        <div class="sidebar-empty">
          <span>Failed to load commits.</span>
          <button class="btn btn-secondary" onclick="window.understandingAgent.fetchCommits()">Retry</button>
        </div>
      `;
    }
  }

  async function fetchCommitDetails(commitId) {
    try {
      const res = await fetch(`/api/commits/${encodeURIComponent(commitId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('Failed to fetch commit details:', err);
      return null;
    }
  }

  async function fetchAttemptDetails(attemptId) {
    try {
      const res = await fetch(`/api/attempts/${encodeURIComponent(attemptId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('Failed to fetch attempt details:', err);
      return null;
    }
  }


  // Rendering


  function renderSidebar() {
    const q = state.searchQuery.toLowerCase().trim();
    // Guarantee only passed committed commits are shown on the left side
    const passedCommits = state.commits.filter((c) => {
      if (c.is_staged || c.id === 'staged') return false;
      const st = (c.status || '').toUpperCase();
      return st === 'PASSED';
    });

    const filtered = passedCommits.filter((c) => {
      if (!q) return true;
      return (
        (c.message && c.message.toLowerCase().includes(q)) ||
        (c.short_hash && c.short_hash.toLowerCase().includes(q)) ||
        (c.hash && c.hash.toLowerCase().includes(q)) ||
        (c.author && c.author.toLowerCase().includes(q))
      );
    });

    el.commitsCount.textContent = filtered.length;

    if (filtered.length === 0) {
      el.commitList.innerHTML = `
        <div class="sidebar-empty">
          <span>No passed commits found.</span>
        </div>
      `;
      return;
    }

    el.commitList.innerHTML = filtered
      .map((c) => {
        const isActive = c.id === state.selectedCommitId;
        const attemptsText =
          c.attempts_count === 1
            ? '1 attempt'
            : c.attempts_count > 1
              ? `${c.attempts_count} attempts`
              : '0 attempts';

        return `
          <div class="commit-card ${isActive ? 'active' : ''}" 
               data-id="${escapeHtml(c.id)}"
               onclick="window.understandingAgent.selectCommit('${escapeHtml(c.id)}')">
            <div class="commit-card-top">
              <span class="commit-card-status status-passed">
                ✓ PASSED
              </span>
              <span class="commit-card-hash">${escapeHtml(c.short_hash || 'commit')}</span>
            </div>
            <div class="commit-card-msg" title="${escapeHtml(c.message)}">
              ${escapeHtml(c.message || 'No commit message')}
            </div>
            <div class="commit-card-bottom">
              <span class="commit-card-author">${escapeHtml(c.author || '')}</span>
              <span class="commit-card-attempts">${attemptsText}</span>
            </div>
          </div>
        `;
      })
      .join('');
  }

  async function selectCommit(commitId, forceReload = false) {
    if (state.selectedCommitId === commitId && !forceReload && state.selectedCommitData) {
      showOverview();
      return;
    }

    state.selectedCommitId = commitId;
    renderSidebar();

    // Show loading in overview title
    el.emptyState.classList.add('hidden');
    el.attemptDetailView.classList.add('hidden');
    el.commitOverviewView.classList.remove('hidden');
    el.crumbCommitTitle.textContent = 'Loading commit...';
    el.overviewMsgTitle.textContent = 'Loading commit details...';

    const data = await fetchCommitDetails(commitId);
    if (!data) {
      el.overviewMsgTitle.textContent = 'Failed to load commit data.';
      return;
    }

    state.selectedCommitData = data;
    renderCommitOverview(data);
  }

  function renderCommitOverview(data) {
    showOverview();

    // Header info
    const fullMsg = data.message || '';
    const lines = fullMsg.split('\n');
    const title = lines[0] || 'Commit';
    const body = lines.slice(1).join('\n').trim();

    el.crumbCommitTitle.textContent = `${data.short_hash || 'commit'}: ${title}`;
    el.overviewMsgTitle.textContent = title;

    if (body) {
      el.overviewMsgBody.textContent = body;
      el.overviewMsgBody.classList.remove('hidden');
    } else {
      el.overviewMsgBody.classList.add('hidden');
    }

    const st = (data.status || 'PASSED').toUpperCase();
    el.overviewStatusBadge.textContent = st;
    el.overviewStatusBadge.className = 'commit-status-badge';
    if (st === 'PASSED') el.overviewStatusBadge.classList.add('status-passed');
    else if (st === 'FAILED') el.overviewStatusBadge.classList.add('status-failed');
    else el.overviewStatusBadge.classList.add('status-pending');

    el.overviewBranchBadge.textContent = data.branch || 'main';
    el.overviewHash.textContent = data.hash || 'staged';
    el.overviewAuthor.textContent = data.author || 'Unknown';
    el.overviewTimestamp.textContent = formatDate(data.timestamp);

    // Changed Files
    const files = data.files || [];
    el.filesCountBadge.textContent = `${files.length} file${files.length === 1 ? '' : 's'}`;
    if (files.length === 0) {
      el.filesList.innerHTML = `<div class="sidebar-empty">No changed files detected.</div>`;
    } else {
      el.filesList.innerHTML = files
        .map((f) => {
          const status = (f.status || 'modified').toLowerCase();
          return `
            <div class="file-row">
              <div class="file-info">
                <span class="file-chip ${status}">${status.slice(0, 3)}</span>
                <span class="file-path">${escapeHtml(f.path)}</span>
              </div>
              <div class="file-stats">
                <span class="stat-add">+${f.additions || 0}</span>
                <span class="stat-del">-${f.deletions || 0}</span>
              </div>
            </div>
          `;
        })
        .join('');
    }

    // Change Summary
    const sum = data.summary || {};
    el.sumWhatChanged.textContent = sum.what_changed || 'Summary not available.';
    el.sumImpact.textContent = sum.impact || 'No specific architectural impact noted.';
    el.sumWhy.textContent = sum.why_it_matters || 'Commit modifications documented in repository history.';
    el.sumRisks.textContent = sum.key_risks || 'Standard code review recommended.';

    // Pre-Commit Attempts
    const attempts = data.attempts || [];
    el.attemptsCountBadge.textContent = `${attempts.length} attempt${attempts.length === 1 ? '' : 's'}`;

    if (attempts.length === 0) {
      el.attemptsGrid.innerHTML = `
        <div class="sidebar-empty">
          <span>No pre-commit interactive defense attempts recorded for this commit.</span>
        </div>
      `;
    } else {
      el.attemptsGrid.innerHTML = attempts
        .map((att, idx) => {
          const attStatus = (att.status || 'FAILED').toUpperCase();
          const isPass = attStatus === 'PASSED';
          const violationsCount = att.violations_count || 0;
          const score = Math.round(att.score || 0);

          return `
            <div class="attempt-card" onclick="window.understandingAgent.openAttempt('${escapeHtml(att.attempt_id)}')">
              <div class="attempt-card-info">
                <span class="attempt-num">Attempt #${att.attempt_number || idx + 1}</span>
                <span class="commit-status-badge ${isPass ? 'status-passed' : 'status-failed'}">
                  ${attStatus}
                </span>
                <span class="badge-count ${violationsCount > 0 ? 'badge-danger' : 'badge-success'}">
                  ${violationsCount} violation${violationsCount === 1 ? '' : 's'}
                </span>
                <span class="attempt-score-tag">Score: ${score}%</span>
                <span class="attempt-time">${formatDate(att.timestamp)}</span>
              </div>
              <button class="btn btn-secondary">
                View Attempt Details →
              </button>
            </div>
          `;
        })
        .join('');
    }
  }

  async function openAttempt(attemptId) {
    const data = await fetchAttemptDetails(attemptId);
    if (!data) return;

    state.selectedAttemptId = attemptId;
    state.selectedAttemptData = data;

    showAttemptDetail();

    // Update Header
    const attNo = data.attempt_number || 1;
    el.attemptTitle.textContent = `Attempt #${attNo}`;
    el.crumbCommitTitle.textContent = `${state.selectedCommitData ? state.selectedCommitData.short_hash : 'commit'} > Attempt #${attNo}`;

    const st = (data.status || 'FAILED').toUpperCase();
    el.attemptStatusBadge.textContent = st;
    el.attemptStatusBadge.className = 'commit-status-badge';
    if (st === 'PASSED') el.attemptStatusBadge.classList.add('status-passed');
    else el.attemptStatusBadge.classList.add('status-failed');

    el.attemptScoreBadge.textContent = `Score: ${Math.round(data.score || 0)}%`;
    el.attemptTimestamp.textContent = formatDate(data.timestamp);

    // Navigation buttons
    const nav = data.navigation || {};
    el.btnPrevAttempt.disabled = !nav.previous_attempt_id;
    el.btnNextAttempt.disabled = !nav.next_attempt_id;

    el.btnPrevAttempt.onclick = () => {
      if (nav.previous_attempt_id) openAttempt(nav.previous_attempt_id);
    };
    el.btnNextAttempt.onclick = () => {
      if (nav.next_attempt_id) openAttempt(nav.next_attempt_id);
    };

    // Attempt stepper
    const commitAttempts = state.selectedCommitData ? state.selectedCommitData.attempts || [] : [];
    if (commitAttempts.length > 1) {
      el.attemptStepper.innerHTML = commitAttempts
        .map((ca, idx) => {
          const isActive = (ca.attempt_id === attemptId);
          return `
            <button class="step-btn ${isActive ? 'active' : ''}" 
                    onclick="window.understandingAgent.openAttempt('${escapeHtml(ca.attempt_id)}')">
              Attempt #${ca.attempt_number || idx + 1}
            </button>
          `;
        })
        .join('');
    } else {
      el.attemptStepper.innerHTML = '';
    }

    // Render ChatGPT Conversation Thread
    renderConversation(data.questions || []);

    // Render Violations & Coding Standards
    renderViolations(data.violations || [], data.standards_report || []);
  }

  function renderConversation(questions) {
    if (!questions || questions.length === 0) {
      el.chatThread.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--text-secondary); background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color);">
          <span>No question/answer defenses recorded for this attempt.</span>
        </div>
      `;
      return;
    }

    el.chatThread.innerHTML = questions
      .map((q, idx) => {
        const qType = q.type || 'Invariants';
        const qText = q.question || 'Explain this change.';
        const timeLimit = q.time_limit_seconds || q.time_limit || 60;
        const ansText = (q.answer || '').trim();
        const evalObj = q.evaluation || {};
        const score = evalObj.score !== undefined ? Math.round(evalObj.score) : null;
        const passed = evalObj.passed || (score !== null && score >= 70);
        const feedback = evalObj.qualitative_feedback || evalObj.feedback || '';
        const responseTime = q.response_time_seconds ? Math.round(q.response_time_seconds) : null;

        // Determine user state
        const rawStatus = (q.status || '').toLowerCase();
        const isTimedOut = rawStatus === 'timeout' || rawStatus === 'timed_out' || q.timed_out === true;
        const isAnswered = Boolean(ansText) || rawStatus === 'answered';
        const isPending = !isAnswered && !isTimedOut;

        // Status badge & score badge
        let statusBadgeHtml = '';
        let scoreBadgeHtml = '';

        if (isAnswered) {
          statusBadgeHtml = `<span class="qa-status-pill qa-status-answered">Answered</span>`;
          if (score !== null) {
            scoreBadgeHtml = `<span class="qa-score-badge ${passed ? 'qa-score-pass' : 'qa-score-fail'}">Score: ${score}%</span>`;
          }
        } else if (isTimedOut) {
          statusBadgeHtml = `<span class="qa-status-pill qa-status-timeout">Timed Out</span>`;
          scoreBadgeHtml = `<span class="qa-score-badge qa-score-zero">0%</span>`;
        } else {
          statusBadgeHtml = `<span class="qa-status-pill qa-status-pending">Pending Defense</span>`;
        }

        // Answer / Response section
        let responseContentHtml = '';

        if (isAnswered) {
          responseContentHtml = `
            <div class="qa-answer-block">
              <div class="qa-answer-meta">
                <span class="qa-answer-author">Developer Response</span>
                ${responseTime ? `<span class="qa-time-tag">${responseTime}s response</span>` : ''}
              </div>
              <div class="qa-answer-text">${escapeHtml(ansText)}</div>
              ${feedback ? `
                <div class="qa-verdict-line">
                  <span class="qa-verdict-label">Evaluation:</span>
                  <span class="qa-verdict-text">${escapeHtml(feedback)}</span>
                </div>
              ` : ''}
            </div>
          `;
        } else if (isTimedOut) {
          responseContentHtml = `
            <div class="qa-notice-block qa-notice-timeout">
              <div class="qa-notice-body">
                <span class="qa-notice-title">Timed Out</span>
                <span class="qa-notice-desc">Response window expired (${timeLimit}s limit) without an answer being received.</span>
              </div>
            </div>
          `;
        } else {
          responseContentHtml = `
            <div class="qa-notice-block qa-notice-pending">
              <div class="qa-notice-body">
                <span class="qa-notice-title">Pending Defense</span>
                <span class="qa-notice-desc">Awaiting developer defense during git pre-commit verification.</span>
              </div>
            </div>
          `;
        }

        // Follow-up question if any
        let followUpHtml = '';
        if (q.follow_up && q.follow_up.question) {
          const fuAns = (q.follow_up.answer || '').trim();
          followUpHtml = `
            <div class="qa-followup-block">
              <div class="qa-followup-header">
                <span class="qa-followup-tag">↳ Follow-Up Question</span>
              </div>
              <div class="qa-followup-question">${escapeHtml(q.follow_up.question)}</div>
              <div class="qa-followup-answer">
                <span class="qa-followup-label">Developer:</span>
                <span class="qa-followup-text">${escapeHtml(fuAns || 'No answer recorded')}</span>
              </div>
            </div>
          `;
        }

        return `
          <div class="qa-card ${isAnswered ? 'qa-card-answered' : isTimedOut ? 'qa-card-timeout' : 'qa-card-pending'}">
            <div class="qa-card-header">
              <div class="qa-header-left">
                <span class="qa-num-badge">Q${idx + 1}</span>
                <span class="qa-type-badge">${escapeHtml(qType)}</span>
                <span class="qa-limit-badge">${timeLimit}s limit</span>
              </div>
              <div class="qa-header-right">
                ${statusBadgeHtml}
                ${scoreBadgeHtml}
              </div>
            </div>

            <div class="qa-question-title">${escapeHtml(qText)}</div>

            ${responseContentHtml}
            ${followUpHtml}
          </div>
        `;
      })
      .join('');
  }

  function formatInlineCode(str) {
    if (!str) return '';
    return escapeHtml(str).replace(/`([^`]+)`/g, '<code class="audit-inline-code">$1</code>');
  }

  function formatViolationItem(rawText) {
    const regex = /^([a-zA-Z0-9_\-\.\/]+\.[a-zA-Z0-9]+):(\d+):\s*(.*)$/;
    const match = String(rawText).match(regex);
    if (match) {
      const file = match[1];
      const line = match[2];
      const msg = formatInlineCode(match[3]);
      return `
        <div class="audit-item-line">
          <span class="audit-item-arrow">↳</span>
          <span class="audit-file-pill">
            <span class="audit-file-path">${escapeHtml(file)}</span><span class="audit-file-line">:${escapeHtml(line)}</span>
          </span>
          <span class="audit-item-msg">${msg}</span>
        </div>
      `;
    }
    return `
      <div class="audit-item-line">
        <span class="audit-item-arrow">↳</span>
        <span class="audit-item-msg">${formatInlineCode(rawText)}</span>
      </div>
    `;
  }

  function renderViolations(violations, standardsReport = []) {
    let report = Array.isArray(standardsReport) && standardsReport.length > 0 ? [...standardsReport] : [];

    // Fallback if standardsReport was not provided directly
    if (report.length === 0 && violations && violations.length > 0) {
      const grouped = {};
      for (const v of violations) {
        const std = v.standard || 'General Standard';
        if (!grouped[std]) grouped[std] = [];
        const loc = v.file ? `${v.file}${v.line ? `:${v.line}` : ''}: ` : '';
        grouped[std].push(`${loc}${v.description || v.details}`);
      }
      for (const [name, viols] of Object.entries(grouped)) {
        report.push({
          name: name,
          is_good: false,
          status: 'FAILED',
          category: 'Coding Standard',
          violations: viols,
        });
      }
    }

    state.lastStandardsReport = report;

    const passed = report.filter((s) => s.is_good);
    const failed = report.filter((s) => !s.is_good);
    const total = report.length;

    // Update filter counters
    if (el.filterCountAll) el.filterCountAll.textContent = total;
    if (el.filterCountFailed) el.filterCountFailed.textContent = failed.length;
    if (el.filterCountPassed) el.filterCountPassed.textContent = passed.length;

    // Update terminal header badges
    if (el.termBadgeFailed) el.termBadgeFailed.textContent = `${failed.length} Failed`;
    if (el.termBadgePassed) el.termBadgePassed.textContent = `${passed.length} Passed`;

    // Update compliance bar
    const compliancePct = total > 0 ? Math.round((passed.length / total) * 100) : (failed.length === 0 ? 100 : 0);
    if (el.termComplianceBar) el.termComplianceBar.style.width = `${compliancePct}%`;
    if (el.termComplianceLabel) el.termComplianceLabel.textContent = `${compliancePct}% Compliance Rate`;

    // Update terminal summary counts
    if (el.termSummaryCounts) {
      el.termSummaryCounts.innerHTML = `
        <span class="summary-pass-text">${passed.length} Passed (Good)</span>
        <span class="summary-sep">│</span>
        <span class="summary-fail-text">${failed.length} Failed (Violations)</span>
      `;
    }

    if (report.length === 0) {
      el.attemptViolationsList.innerHTML = `
        <div style="padding: 20px; text-align: center; color: var(--text-secondary);">
          <span style="font-size: 14px; font-weight: 600; color: var(--accent-green);">No coding standards evaluated or detected for this attempt.</span>
        </div>
      `;
      return;
    }

    let html = '';

    // 1. Failed Standards First
    for (const s of failed) {
      const viols = s.violations && s.violations.length > 0 ? s.violations : [s.details || 'Violation detected'];
      const cat = s.category ? `<span class="audit-category-tag">${escapeHtml(s.category)}</span>` : '';
      html += `
        <div class="audit-group audit-group-failed" data-status="failed">
          <div class="audit-header-line">
            <span class="audit-fail-badge">FAILED</span>
            <span class="audit-std-name">${escapeHtml(s.name)}</span>
            ${cat}
          </div>
          ${viols.map((v) => formatViolationItem(v)).join('')}
        </div>
      `;
    }

    // 2. Passed Standards
    for (const s of passed) {
      const detailText = s.details || 'Standard verified compliant; no anti-patterns detected.';
      const cat = s.category ? `<span class="audit-category-tag">${escapeHtml(s.category)}</span>` : '';
      html += `
        <div class="audit-group audit-group-passed" data-status="passed">
          <div class="audit-header-line">
            <span class="audit-pass-badge">PASSED</span>
            <span class="audit-std-name">${escapeHtml(s.name)}</span>
            ${cat}
          </div>
          <div class="audit-item-line">
            <span class="audit-item-arrow">↳</span>
            <span class="audit-item-msg audit-passed-msg">${formatInlineCode(detailText)}</span>
          </div>
        </div>
      `;
    }

    el.attemptViolationsList.innerHTML = html;
    applyAuditFilter(state.auditFilter);
  }

  function applyAuditFilter(filter) {
    state.auditFilter = filter;
    // Update active button state
    [el.btnFilterAll, el.btnFilterFailed, el.btnFilterPassed].forEach((btn) => {
      if (!btn) return;
      if (btn.getAttribute('data-filter') === filter) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    const groups = el.attemptViolationsList.querySelectorAll('.audit-group');
    groups.forEach((g) => {
      const st = g.getAttribute('data-status');
      if (filter === 'all') {
        g.style.display = '';
      } else if (filter === 'failed') {
        g.style.display = st === 'failed' ? '' : 'none';
      } else if (filter === 'passed') {
        g.style.display = st === 'passed' ? '' : 'none';
      }
    });
  }

  function copyAsciiAuditReport() {
    const report = state.lastStandardsReport || [];
    if (!report || report.length === 0) return;

    const failed = report.filter((s) => !s.is_good);
    const passed = report.filter((s) => s.is_good);
    const lines = [];

    for (const s of failed) {
      lines.push(`FAILED  ${s.name}`);
      const viols = s.violations && s.violations.length > 0 ? s.violations : [s.details || 'Violation detected'];
      for (const v of viols) {
        lines.push(`         ↳ ${v}`);
      }
    }

    for (const s of passed) {
      lines.push(`PASSED  ${s.name}`);
      lines.push(`         ↳ ${s.details || 'Standard compliant; no violations detected.'}`);
    }

    lines.push('');
    lines.push(`Summary: ${passed.length} Passed (Good) │ ${failed.length} Failed (Violations)`);

    const fullText = lines.join('\n');
    navigator.clipboard.writeText(fullText).then(() => {
      const textEl = document.getElementById('btn-copy-terminal-text');
      if (textEl) {
        textEl.textContent = 'Copied!';
        setTimeout(() => {
          textEl.textContent = 'Copy Output';
        }, 2000);
      }
    });
  }

  function showOverview() {
    state.currentView = 'overview';
    el.attemptDetailView.classList.add('hidden');
    el.commitOverviewView.classList.remove('hidden');
    if (state.selectedCommitData) {
      const fullMsg = state.selectedCommitData.message || '';
      const title = fullMsg.split('\n')[0] || 'Commit';
      el.crumbCommitTitle.textContent = `${state.selectedCommitData.short_hash || 'commit'}: ${title}`;
    }
  }

  function showAttemptDetail() {
    state.currentView = 'attempt';
    el.commitOverviewView.classList.add('hidden');
    el.attemptDetailView.classList.remove('hidden');
  }

  function copyHash() {
    const hash = el.overviewHash.textContent;
    if (hash && hash !== '--------') {
      navigator.clipboard.writeText(hash).then(() => {
        const orig = el.btnCopyHash.innerHTML;
        el.btnCopyHash.innerHTML = `<svg class="copy-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--accent-green);"><polyline points="20 6 9 17 4 12"/></svg>`;
        setTimeout(() => {
          el.btnCopyHash.innerHTML = orig;
        }, 1500);
      });
    }
  }

  // Event Listeners & Init


  function init() {
    // Search filter
    el.commitSearch.addEventListener('input', (e) => {
      state.searchQuery = e.target.value;
      renderSidebar();
    });

    // Refresh button
    el.refreshBtn.addEventListener('click', () => {
      fetchCommits(true);
    });

    // Back button from attempt to commit overview
    el.btnBackToCommit.addEventListener('click', showOverview);

    // Copy hash button
    el.btnCopyHash.addEventListener('click', copyHash);

    // Audit filter pill clicks
    if (el.btnFilterAll) el.btnFilterAll.addEventListener('click', () => applyAuditFilter('all'));
    if (el.btnFilterFailed) el.btnFilterFailed.addEventListener('click', () => applyAuditFilter('failed'));
    if (el.btnFilterPassed) el.btnFilterPassed.addEventListener('click', () => applyAuditFilter('passed'));

    // Copy terminal audit report button
    if (el.btnCopyTerminal) el.btnCopyTerminal.addEventListener('click', copyAsciiAuditReport);

    // Initial load
    fetchCommits();

    // Auto-refresh every 10 seconds to catch newly recorded sessions seamlessly
    setInterval(() => {
      fetchCommits(true);
    }, 10000);
  }

  // Expose global methods
  window.understandingAgent = {
    fetchCommits,
    selectCommit,
    openAttempt,
  };

  // Start
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
