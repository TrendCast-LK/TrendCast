import { Routes, Route, Navigate } from "react-router-dom";
import SignIn from "./pages/SignIn";
import SignUp from "./pages/SignUp";
import Dashboard from "./pages/Dashboard";
import NewPrediction from "./pages/NewPrediction";
import PredictionResult from "./pages/PredictionResult";
import Trends from "./pages/Trends";
import Settings from "./pages/Settings";
import Channel from "./pages/Channel";
import RequireAuth from "./components/RequireAuth";
import RequireAdmin from "./components/admin/RequireAdmin";
import AdminLogin from "./pages/admin/AdminLogin";
import AdminOverview from "./pages/admin/AdminOverview";
import AdminUsers from "./pages/admin/AdminUsers";
import AdminUserDetail from "./pages/admin/AdminUserDetail";
import AdminActivity from "./pages/admin/AdminActivity";
import AdminPredictions from "./pages/admin/AdminPredictions";
import AdminPredictionDetail from "./pages/admin/AdminPredictionDetail";
import AdminCache from "./pages/admin/AdminCache";

function admin(page) {
  return <RequireAdmin>{page}</RequireAdmin>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<SignIn />} />
      <Route path="/sign-up" element={<SignUp />} />

      <Route
        path="/dashboard"
        element={
          <RequireAuth>
            <Dashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/new-prediction"
        element={
          <RequireAuth>
            <NewPrediction />
          </RequireAuth>
        }
      />
      <Route
        path="/prediction-result/:id"
        element={
          <RequireAuth>
            <PredictionResult />
          </RequireAuth>
        }
      />
      <Route
        path="/trends"
        element={
          <RequireAuth>
            <Trends />
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <Settings />
          </RequireAuth>
        }
      />

      <Route
        path="/channel"
        element={
          <RequireAuth>
            <Channel />
          </RequireAuth>
        }
      />

      {/* Admin dashboard: separate login and session (AdminAuthContext). */}
      <Route path="/admin/login" element={<AdminLogin />} />
      <Route path="/admin" element={admin(<AdminOverview />)} />
      <Route path="/admin/users" element={admin(<AdminUsers />)} />
      <Route path="/admin/users/:id" element={admin(<AdminUserDetail />)} />
      <Route path="/admin/predictions" element={admin(<AdminPredictions />)} />
      <Route path="/admin/predictions/:id" element={admin(<AdminPredictionDetail />)} />
      <Route path="/admin/cache" element={admin(<AdminCache />)} />
      <Route path="/admin/activity" element={admin(<AdminActivity />)} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
