// The owner's tabs before the app can change the network: the panel does it for now.
import { useTexts } from "../i18n";

interface OwnerLaterProps {
  host: string;
  what: string;
}

export function OwnerLater({ host, what }: OwnerLaterProps) {
  const texts = useTexts();
  return (
    <main className="screen">
      <header className="head">
        <h1>{what}</h1>
      </header>
      <section className="card">
        <p>{texts.ownerLater}</p>
        <p className="muted">
          {texts.ownerLaterPanel} <span className="mono">https://{host}/</span>
        </p>
      </section>
    </main>
  );
}
