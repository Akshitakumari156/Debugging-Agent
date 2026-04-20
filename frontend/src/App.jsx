import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import { Bot } from 'lucide-react';
import Dashboard from './pages/Dashboard';
import TaskDetails from './pages/TaskDetails';

function App() {
  return (
    <Router>
      <div className="app-container">
        <header className="header">
          <Link to="/" className="logo">
            <Bot className="logo-icon" size={28} />
            <span>AutoDebug</span>
          </Link>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            Autonomous issue resolution agent
          </div>
        </header>
        
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/tasks/:taskId" element={<TaskDetails />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
