import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { Play, Activity, CheckCircle2, XCircle, Clock, Search, ExternalLink } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export default function Dashboard() {
  const navigate = useNavigate();
  const [repoUrl, setRepoUrl] = useState('');
  const [issueId, setIssueId] = useState('');
  const [issueDesc, setIssueDesc] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [tasks, setTasks] = useState([]);
  const [loadingTasks, setLoadingTasks] = useState(true);

  const fetchTasks = async () => {
    try {
      const res = await axios.get(`${API_URL}/api/tasks`);
      setTasks(res.data.tasks);
    } catch (err) {
      console.error('Failed to fetch tasks', err);
    } finally {
      setLoadingTasks(false);
    }
  };

  useEffect(() => {
    fetchTasks();
    const interval = setInterval(fetchTasks, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!repoUrl || (!issueId && !issueDesc)) return;
    
    setIsSubmitting(true);
    try {
      const res = await axios.post(`${API_URL}/api/run-task`, {
        repo_url: repoUrl,
        issue_id: issueId ? parseInt(issueId) : null,
        issue_description: issueDesc || null,
      });
      navigate(`/tasks/${res.data.task_id}`);
    } catch (err) {
      console.error(err);
      alert('Failed to start task. See console.');
      setIsSubmitting(false);
    }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '40px' }}>
      
      {/* New Task Form */}
      <div>
        <div className="glass-panel" style={{ padding: '30px' }}>
          <h2 style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Play size={24} style={{ color: 'var(--brand-primary)' }} />
            New Debug Task
          </h2>
          
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label">Repository URL</label>
              <input 
                type="text" 
                className="form-input" 
                placeholder="https://github.com/owner/repo"
                value={repoUrl}
                onChange={e => setRepoUrl(e.target.value)}
                required
              />
            </div>
            
            <div className="form-group">
              <label className="form-label">Issue Number (Optional)</label>
              <input 
                type="number" 
                className="form-input" 
                placeholder="e.g. 42"
                value={issueId}
                onChange={e => setIssueId(e.target.value)}
              />
            </div>
            
            <div className="form-group">
              <label className="form-label">Issue Description (If no ID)</label>
              <textarea 
                className="form-textarea" 
                placeholder="Describe the bug here if it's not a GitHub issue..."
                value={issueDesc}
                onChange={e => setIssueDesc(e.target.value)}
              />
            </div>
            
            <button 
              type="submit" 
              className="btn btn-primary pulse-active" 
              style={{ width: '100%', marginTop: '10px' }}
              disabled={isSubmitting || !repoUrl || (!issueId && !issueDesc)}
            >
              {isSubmitting ? <span className="spin">⚡</span> : <Play size={18} />}
              {isSubmitting ? 'Starting Agent...' : 'Launch Agent'}
            </button>
          </form>
        </div>
      </div>

      {/* Task History */}
      <div>
        <div className="glass-panel" style={{ padding: '30px', minHeight: '600px' }}>
          <h2 style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Activity size={24} style={{ color: 'var(--brand-primary)' }} />
            Task History
          </h2>
          
          {loadingTasks ? (
            <div style={{ color: 'var(--text-muted)' }}>Loading tasks...</div>
          ) : tasks.length === 0 ? (
            <div style={{ color: 'var(--text-muted)' }}>No tasks run yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
              {tasks.map(task => (
                <div 
                  key={task.task_id} 
                  className="glass-panel" 
                  style={{ 
                    padding: '20px', 
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    background: 'rgba(255,255,255,0.02)'
                  }}
                  onClick={() => navigate(`/tasks/${task.task_id}`)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '10px' }}>
                    <div style={{ fontWeight: 600, fontSize: '1.1rem' }}>
                      {task.repo_url.split('/').slice(-2).join('/')} 
                      {task.issue_id ? ` #${task.issue_id}` : ''}
                    </div>
                    <StatusBadge status={task.status} />
                  </div>
                  
                  <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '15px' }}>
                    {task.issue_title || task.issue_body?.substring(0, 100) || 'Unknown issue'}
                  </div>
                  
                  <div style={{ display: 'flex', gap: '20px', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                      <Clock size={14} />
                      {task.created_at ? formatDistanceToNow(new Date(task.created_at), { addSuffix: true }) : ''}
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                      <Search size={14} />
                      {task.current_node ? `Node: ${task.current_node}` : 'Complete'}
                    </span>
                    {task.pr_url && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--brand-primary)' }}>
                        <ExternalLink size={14} />
                        PR Created
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      
    </div>
  );
}

function StatusBadge({ status }) {
  let icon = null;
  let text = status.toUpperCase();
  
  if (status === 'completed') {
    icon = <CheckCircle2 size={14} />;
  } else if (status === 'failed' || status === 'cancelled') {
    icon = <XCircle size={14} />;
  } else {
    icon = <span className="spin">⚡</span>;
    // pretty up inner states
    if (status !== 'pending') text = `AGENT: ${status.toUpperCase()}`;
  }
  
  return (
    <span className={`status-badge status-${status.toLowerCase()}`}>
      {icon} {text}
    </span>
  );
}
