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

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
