import { Route, Routes } from "react-router-dom";

import AppShell from "./components/AppShell";
import Dashboard from "./pages/Dashboard";
import ExecutionMonitor from "./pages/ExecutionMonitor";
import ImpactAnalysis from "./pages/ImpactAnalysis";
import NotFound from "./pages/NotFound";
import RegulationDetail from "./pages/RegulationDetail";
import RegulatoryExplorer from "./pages/RegulatoryExplorer";
import Settings from "./pages/Settings";
import Simulator from "./pages/Simulator";
import TestManagement from "./pages/TestManagement";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="/regulations" element={<RegulatoryExplorer />} />
        <Route path="/regulations/:documentId" element={<RegulationDetail />} />
        <Route path="/impact" element={<ImpactAnalysis />} />
        <Route path="/tests" element={<TestManagement />} />
        <Route path="/executions" element={<ExecutionMonitor />} />
        <Route path="/executions/:runId" element={<ExecutionMonitor />} />
        {/* The simulator uses a nested tab key so deep links land on a module. */}
        <Route path="/simulator" element={<Simulator />} />
        <Route path="/simulator/:section" element={<Simulator />} />
        <Route path="/simulator/:section/:entityId" element={<Simulator />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
