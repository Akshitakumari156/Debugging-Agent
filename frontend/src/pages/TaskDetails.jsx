import { useState, useEffect, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import axios from 'axios';
import { Terminal, ArrowLeft, GitPullRequest, Search, FileCode, CheckCircle2, XCircle } from 'lucide-react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';

export default function TaskDetails() {
  const { taskId } = useParams();
  const [task, setTask] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  
  const logsEndRef = useRef(null);

  // Fetch initial state
  useEffect(() => {
    const fetchTask = async () => {
      try {
        const [taskRes, logsRes] = await Promise.all([
          axios.get(`${API_URL}/api/task-status/${taskId}`),
          axios.get(`${API_URL}/api/logs/${taskId}`)
        ]);
        setTask(taskRes.data);
        setLogs(logsRes.data.logs);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchTask();
  }, [taskId]);

  // WebSocket for live updates
  useEffect(() => {
    if (!taskId) return;
    
    // We already got historical logs, now we stream the rest.
    // A robust impl would pass after_id to the REST stream URL,
    // but here we just connect raw WS. The backend WS endpoint doesn't send historicals,
    // just broadcasts live events. 
    // In our backend impl, the REST SSE endpoint handles trailing logs better,
    // but let's use the explicit raw websocket we built:
    const ws = new WebSocket(`${WS_URL}/ws/${taskId}`);
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.event === 'log') {
        setLogs(prev => [...prev, data]);
        // Sync the active node in the graph visualization
        if (data.node) {
          setTask(prev => ({ ...prev, current_node: data.node }));
        }
      } else if (data.event === 'task_complete') {
        // refresh task to get final PR url
        axios.get(`${API_URL}/api/task-status/${taskId}`)
          .then(res => setTask({ ...res.data, status: data.status, pr_url: data.pr_url }));
      }
    };

    return () => ws.close();
  }, [taskId]);

  // Auto-scroll logs
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  if (loading) return <div>Loading agent state...</div>;
  if (!task) return <div>Task not found</div>;

  return (
    <div className="task-details-page">
      <Link to="/" className="btn btn-secondary" style={{ marginBottom: '20px' }}>
        <ArrowLeft size={16} /> Back to Dashboard
      </Link>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: '30px' }}>
        
        {/* Main Content Area */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '30px' }}>
          
          {/* Header Info */}
          <div className="glass-panel" style={{ padding: '30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <h1 className="page-title" style={{ marginBottom: '10px' }}>
                  {task.repo_url.split('/').slice(-2).join('/')}
                  {task.issue_id && <span style={{ color: 'var(--text-muted)' }}> #{task.issue_id}</span>}
                </h1>
                <p style={{ color: 'var(--text-secondary)' }}>
                  {task.issue_title || 'No issue title'}
                </p>
              </div>
              <StatusBadge status={task.status} />
            </div>

            {task.pr_url && (
              <div style={{ 
                marginTop: '20px', 
                padding: '15px 20px', 
                background: 'rgba(16, 185, 129, 0.1)', 
                border: '1px solid rgba(16, 185, 129, 0.3)',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                gap: '15px'
              }}>
                <div style={{ background: '#10b981', color: 'black', padding: '8px', borderRadius: '50%' }}>
                  <GitPullRequest size={20} />
                </div>
                <div>
                  <div style={{ fontWeight: 600, color: '#10b981' }}>Success! Pull Request Created</div>
                  <a href={task.pr_url} target="_blank" rel="noreferrer" style={{ fontSize: '0.9rem' }}>
                    {task.pr_url}
                  </a>
                </div>
              </div>
            )}
            
            {task.error_message && (
              <div style={{ 
                marginTop: '20px', 
                padding: '15px 20px', 
                background: 'rgba(239, 68, 68, 0.1)', 
                border: '1px solid rgba(239, 68, 68, 0.3)',
                borderRadius: '8px',
                color: '#ef4444'
              }}>
                <strong>Agent Failed:</strong> {task.error_message}
              </div>
            )}
          </div>

          {/* Extracted Context (if available) */}
          {(task.root_cause || task.fix_summary) && (
            <div className="glass-panel" style={{ padding: '30px' }}>
              <h3 style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '20px' }}>
                <Search size={20} style={{ color: 'var(--brand-primary)' }} />
                Agent Analysis
              </h3>
              
              {task.root_cause && (
                <div style={{ marginBottom: '20px' }}>
                  <div style={{ fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '5px' }}>Identified Root Cause:</div>
                  <div style={{ background: 'var(--bg-tertiary)', padding: '15px', borderRadius: '8px', fontSize: '0.95rem' }}>
                    {task.root_cause}
                  </div>
                </div>
              )}
              
              {task.fix_summary && (
                <div>
                  <div style={{ fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '5px' }}>Fix Strategy:</div>
                  <div style={{ background: 'var(--bg-tertiary)', padding: '15px', borderRadius: '8px', fontSize: '0.95rem' }}>
                    {task.fix_summary}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Live Terminal Terminal Logs */}
          <div className="glass-panel" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', height: '600px' }}>
            <div style={{ 
              padding: '15px 20px', 
              background: 'var(--bg-tertiary)', 
              borderBottom: '1px solid var(--border-light)',
              display: 'flex', alignItems: 'center', gap: '10px'
            }}>
              <Terminal size={18} />
              <span style={{ fontWeight: 600, fontFamily: 'var(--font-mono)' }}>Agent Execution Stream</span>
              
              {['pending', 'planning', 'cloning', 'reproducing', 'analyzing', 'fixing', 'testing'].includes(task.status) && (
                <span className="spin" style={{ marginLeft: 'auto', color: 'var(--brand-primary)' }}>⚡</span>
              )}
            </div>
            
            <div style={{ 
              flex: 1, 
              background: '#0d0d12', 
              padding: '20px', 
              overflowY: 'auto',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.85rem',
              color: '#d4d4d8'
            }}>
              {logs.map((log, idx) => (
                <LogEntry key={log.id || idx} log={log} />
              ))}
              <div ref={logsEndRef} />
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          <div className="glass-panel" style={{ padding: '20px' }}>
            <h3 style={{ fontSize: '1rem', marginBottom: '15px', borderBottom: '1px solid var(--border-light)', paddingBottom: '10px' }}>
              Execution Graph
            </h3>
            
            <GraphStep name="Planner" active={task.current_node === 'planner'} done={logs.some(l => l.node === 'planner')} />
            <GraphStep name="Repo Analyzer" active={task.current_node === 'repo_analyzer'} done={logs.some(l => l.node === 'repo_analyzer')} />
            <GraphStep name="Bug Reproducer" active={task.current_node === 'bug_reproducer'} done={logs.some(l => l.node === 'bug_reproducer')} />
            
            {(task.iteration > 0 || logs.some(l => l.node === 'fix_generator')) && (
              <div style={{ marginLeft: '15px', paddingLeft: '15px', borderLeft: '2px dashed var(--border-light)', margin: '10px 0 10px 15px' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--brand-primary)', marginBottom: '5px' }}>
                  RETRY LOOP {task.iteration > 0 ? `(Iter: ${task.iteration})` : ''}
                </div>
                <GraphStep name="Fix Generator" active={task.current_node === 'fix_generator'} done={logs.some(l => l.node === 'fix_generator')} />
                <GraphStep name="Test Runner" active={task.current_node === 'test_runner'} done={logs.some(l => l.node === 'test_runner')} />
                <GraphStep name="Reflector" active={task.current_node === 'reflector'} done={logs.some(l => l.node === 'reflector')} />
              </div>
            )}
            
            <GraphStep name="PR Creator" active={task.current_node === 'pr_creator'} done={logs.some(l => l.node === 'pr_creator')} />
            
          </div>
          
        </div>
      </div>
    </div>
  );
}

function LogEntry({ log }) {
  const time = new Date(log.created_at).toLocaleTimeString([], { hour12: false });
  const colorMap = {
    info: '#60a5fa',
    warning: '#f59e0b',
    error: '#ef4444'
  };
  
  return (
    <div style={{ marginBottom: '10px', display: 'flex', gap: '15px', lineHeight: 1.4 }}>
      <div style={{ color: '#52525b', flexShrink: 0, width: '65px' }}>{time}</div>
      <div style={{ color: colorMap[log.level] || '#fff', flexShrink: 0, width: '110px' }}>
        [{log.node || 'system'}]
      </div>
      <div>
        <div style={{ color: log.level === 'error' ? '#ef4444' : '#d4d4d8' }}>
          {log.message}
        </div>
        
        {/* Render diff patches or JSON conditionally */}
        {(log.metadata?.diff_patch || log.log_metadata?.diff_patch) && (
          <div style={{ marginTop: '10px', marginBottom: '10px' }}>
             <SyntaxHighlighter language="diff" style={vscDarkPlus} customStyle={{ margin: 0, borderRadius: '6px' }}>
                {log.log_metadata?.diff_patch || log.metadata?.diff_patch}
             </SyntaxHighlighter>
          </div>
        )}
        
        {(log.log_metadata?.test_output || log.metadata?.test_output) && log.level === 'info' && (
           <div style={{ marginTop: '5px', padding: '10px', background: '#1e1e1e', borderRadius: '6px', fontSize: '0.8rem', whiteSpace: 'pre-wrap', maxHeight: '150px', overflowY: 'auto' }}>
             {log.log_metadata?.test_output || log.metadata?.test_output}
           </div>
        )}
      </div>
    </div>
  );
}

function GraphStep({ name, active, done }) {
  let color = 'var(--text-muted)';
  let icon = <div style={{ width: 14, height: 14, borderRadius: '50%', border: '2px solid var(--text-muted)' }} />;
  
  if (active) {
    color = 'var(--brand-primary)';
    icon = <span className="spin" style={{ display: 'inline-block', color }}>⚡</span>;
  } else if (done) {
    color = 'var(--status-success)';
    icon = <CheckCircle2 size={16} color={color} />;
  }

  return (
    <div style={{ 
      display: 'flex', alignItems: 'center', gap: '10px', 
      marginBottom: '12px', color,
      fontWeight: active ? 600 : 400
    }}>
      {icon}
      {name}
    </div>
  );
}

function StatusBadge({ status }) {
  let icon = null;
  let text = status.toUpperCase();
  
  if (status === 'completed') {
    icon = <CheckCircle2 size={16} />;
  } else if (status === 'failed' || status === 'cancelled') {
    icon = <XCircle size={16} />;
  } else {
    icon = <span className="spin">⚡</span>;
    if (status !== 'pending') text = `AGENT ACTIVE`;
  }
  
  return (
    <span className={`status-badge status-${status.toLowerCase()}`} style={{ fontSize: '0.9rem', padding: '6px 14px' }}>
      {icon} {text}
    </span>
  );
}
