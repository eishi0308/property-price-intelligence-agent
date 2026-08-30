import { AnalysisView } from '@/components/AnalysisView';

export default async function AnalysisPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AnalysisView analysisId={id} />;
}
