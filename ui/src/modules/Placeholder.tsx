/**
 * A destination whose module is scheduled for a later phase.
 *
 * It states which phase delivers the capability rather than presenting an empty screen
 * or sample data, so nothing on screen is mistaken for a working feature.
 */

import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { Card, Details, StatusChip } from '@/components/primitives';

interface PlaceholderProps {
  title: string;
  phase: string;
  stage: string;
  purpose: string;
  contents: string[];
  depends: string;
}

export function Placeholder({
  title,
  phase,
  stage,
  purpose,
  contents,
  depends,
}: PlaceholderProps) {
  return (
    <>
      <ContextBar
        trail={[title]}
        actions={<StatusChip tone="info">Scheduled</StatusChip>}
      />
      <WorkSurface>
        <PageHeading title={title} detail={purpose} />

        <Card
          title="Not yet delivered"
          subtitle="This destination is listed so the navigation reflects the whole product, not only what is built."
        >
          <Details
            rows={[
              ['Delivered by', `${phase} — ${stage}`],
              ['Depends upon', depends],
              [
                'Will contain',
                <ul key="contents" className="stack gap-1">
                  {contents.map((item) => (
                    <li key={item}>· {item}</li>
                  ))}
                </ul>,
              ],
            ]}
          />
        </Card>
      </WorkSurface>
    </>
  );
}
