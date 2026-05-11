import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy — Helpmate AI",
  description:
    "How Helpmate AI handles your data — what we collect, how we use it, and how we keep it safe.",
};

export default function PrivacyPolicyPage() {
  return (
    <section className="l-sec">
      <div className="l-sec-inner narrow">
        <div className="l-sec-head">
          <div className="eyebrow">Privacy · Effective Apr 17, 2026</div>
          <h2>Privacy Policy</h2>
          <p>
            Helpmate AI is a document question-answering app built to help you
            upload documents, ask grounded questions, and review answers with
            citations and supporting evidence. This policy explains what
            information we collect, how we use it, and how we handle it when
            you use the service.
          </p>
        </div>

        <div className="l-policy">
          <h3>What we collect</h3>
          <p>
            When you sign in with Google, we may receive basic account
            information such as your name, email address, profile image, and
            a unique account identifier. This information is used only to
            authenticate you and connect you to your private workspace.
          </p>
          <p>
            When you upload a file to Helpmate AI, we process the document so
            the app can index it, retrieve relevant evidence, and generate
            grounded answers. This may include storing the uploaded file
            itself, extracted text, document structure, metadata, your
            questions, and the answers, citations, and evidence returned by
            the system.
          </p>
          <p>
            We may also collect limited technical information needed to
            operate the service reliably and securely, such as request
            timestamps, session details, error logs, and basic browser or
            device information.
          </p>

          <h3>How we use it</h3>
          <p>
            We use this information to run the app, maintain your workspace,
            process uploads, generate answers, improve reliability and
            security, and prevent misuse. We do not sell your personal
            information, and we do not use Google account data for
            advertising.
          </p>

          <h3>Google sign-in</h3>
          <p>
            If you sign in with Google, we use Google user data only for
            authentication and account access. We do not access Gmail, Google
            Drive, Google Calendar, or any other Google account content
            unless that is clearly described and separately authorized in the
            future.
          </p>

          <h3>Third-party infrastructure</h3>
          <p>
            To deliver the service, data may be processed and stored using
            third-party infrastructure that supports authentication, hosting,
            storage, retrieval, and model inference. Uploaded content and
            workspace data may be stored temporarily or for a limited
            active-workspace period, depending on how the service is
            configured.
          </p>

          <h3>Sharing and retention</h3>
          <p>
            We may share information only when necessary to operate the app,
            comply with legal obligations, respond to lawful requests, or
            protect the rights, security, and integrity of Helpmate AI and
            its users.
          </p>
          <p>
            We keep information only for as long as it is needed to operate
            the service, maintain active workspaces, meet security needs,
            comply with legal obligations, and resolve disputes. Some
            workspace data may be deleted automatically after a period of
            inactivity or expiration.
          </p>

          <h3>Your choices</h3>
          <p>
            You can choose not to sign in or not to upload documents, but
            some features of Helpmate AI may not work without authentication.
            If you want to ask about your data or request deletion of
            account-linked information, contact us at the email below.
          </p>

          <h3>Security</h3>
          <p>
            We use reasonable technical and organizational measures to
            protect information, but no system can guarantee complete
            security.
          </p>

          <h3>Children</h3>
          <p>
            Helpmate AI is not intended for children under 13, and we do not
            knowingly collect personal information from children under 13.
          </p>

          <h3>Changes to this policy</h3>
          <p>
            We may update this Privacy Policy from time to time. If we make
            important changes, we will update the effective date and publish
            the revised version on this page.
          </p>

          <h3>Contact</h3>
          <p>
            If you have any questions about this Privacy Policy or how
            Helpmate AI handles data, contact{" "}
            <a href="mailto:antony.leander@gmail.com">
              antony.leander@gmail.com
            </a>
            .
          </p>
        </div>
      </div>
    </section>
  );
}
