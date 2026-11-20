// The owner's tabs before the app can change the network: the panel does it for now.

interface OwnerLaterProps {
  host: string;
  what: string;
}

export function OwnerLater({ host, what }: OwnerLaterProps) {
  return (
    <main className="screen">
      <header className="head">
        <h1>{what}</h1>
      </header>
      <section className="card">
        <p>Управлять сетью из приложения можно будет в следующей версии.</p>
        <p className="muted">
          Пока это делается в панели: <span className="mono">https://{host}/</span>
        </p>
      </section>
    </main>
  );
}
