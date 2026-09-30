UPDATE teams SET name = 'Payroll governance' WHERE slug = 'paie' AND name = 'Gouvernance paie';
UPDATE teams SET name = 'Human resources' WHERE slug = 'rh' AND name = 'Ressources humaines';

UPDATE users SET name = 'Demo Admin' WHERE email = 'admin@example.test' AND name = 'Admin Démo';
UPDATE users SET name = 'Clara Contributor' WHERE email = 'clara@example.test' AND name = 'Clara Contributrice';
UPDATE users SET name = 'Diane HR Owner' WHERE email = 'diane@example.test' AND name = 'Diane Owner RH';
UPDATE users SET name = 'Erik Other Team' WHERE email = 'erik@example.test' AND name = 'Erik Autre Équipe';

UPDATE documents SET
    title = 'Payroll onboarding guide',
    description = 'Published internal document, currently up to date.',
    category = 'Process',
    tags_json = '["payroll","onboarding"]',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-ONBOARDING-PAIE';

UPDATE documents SET
    title = 'Draft DSN procedure',
    description = 'Unpublished document in draft.',
    category = 'Process',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-PAIE-DRAFT';

UPDATE documents SET
    title = 'Benefits catalog in review',
    description = 'First proposal in review.',
    category = 'Process',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-AVANTAGES-CHALLENGE';

UPDATE documents SET
    title = 'Sensitive compensation policy',
    description = 'Restricted document with explicit access only.',
    category = 'Process',
    tags_json = '["restricted","compensation"]',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-REMUNERATION-RESTREINT';

UPDATE documents SET
    title = 'Quarterly payroll controls',
    description = 'Published document with an overdue audit.',
    category = 'Process',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-CONTROLES-RETARD';

UPDATE documents SET
    title = 'Social reporting template',
    description = 'Published document with a blocking correction in progress.',
    category = 'Process',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-REPORTING-BLOQUANT';

UPDATE documents SET
    title = 'Payroll variables guide',
    description = 'Document with edit 1.1 in preparation.',
    category = 'Process',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-EDIT-11';

UPDATE documents SET
    title = 'Payroll control policy',
    description = 'Document with major version 2.0 in preparation.',
    category = 'Process',
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-MAJEUR-20';

UPDATE documents SET
    title = 'Old paper check procedure',
    description = 'Archived demo document.',
    category = 'Process',
    archive_reason = CASE WHEN archive_reason = 'Obsolète dans le jeu de démonstration' THEN 'Obsolete in the demo data set' ELSE archive_reason END,
    audit_instructions = 'Check business validity, the owner, the fictional source, and access.',
    audit_checklist_json = '["Placeholder source identified","Active owner","Business checklist reviewed"]'
WHERE id = 'DOC-ARCHIVE-OLD';

UPDATE document_versions
SET title = (SELECT documents.title FROM documents WHERE documents.id = document_versions.document_id),
    description = (SELECT documents.description FROM documents WHERE documents.id = document_versions.document_id)
WHERE document_id IN (
    'DOC-ONBOARDING-PAIE',
    'DOC-PAIE-DRAFT',
    'DOC-AVANTAGES-CHALLENGE',
    'DOC-REMUNERATION-RESTREINT',
    'DOC-CONTROLES-RETARD',
    'DOC-REPORTING-BLOQUANT',
    'DOC-EDIT-11',
    'DOC-MAJEUR-20',
    'DOC-ARCHIVE-OLD'
);

UPDATE document_versions SET description = 'Initial demo draft' WHERE description = 'Brouillon initial de démonstration';
UPDATE document_versions SET change_summary = 'Initial creation' WHERE change_summary = 'Création initiale';
UPDATE document_versions SET change_summary = 'Demo proposal' WHERE change_summary = 'Proposition de démonstration';
UPDATE document_versions SET change_summary = 'Demo publication' WHERE change_summary = 'Publication de démonstration';
UPDATE document_versions SET source_location_label = replace(source_location_label, 'Site SharePoint fictif', 'Fictional SharePoint site') WHERE source_location_label LIKE 'Site SharePoint fictif%';
UPDATE document_versions SET source_location_label = replace(source_location_label, 'Espace fictif', 'Fictional space') WHERE source_location_label LIKE 'Espace fictif%';

UPDATE document_access_grants SET reason = 'Demo owner' WHERE reason = 'Owner de démonstration';
UPDATE document_access_grants SET reason = 'Explicitly authorized reviewer' WHERE reason = 'Reviewer explicitement autorisé';

UPDATE audit_checklist_items SET label = 'Placeholder source identified' WHERE label = 'Source placeholder identifiée';
UPDATE audit_checklist_items SET label = 'Active owner' WHERE label = 'Owner actif';
UPDATE audit_checklist_items SET label = 'Business checklist reviewed' WHERE label = 'Checklist métier revue';
UPDATE audit_checklist_items SET label = 'Check business relevance' WHERE label = 'Vérifier la pertinence';
UPDATE audit_checklist_items SET label = 'Check the owner' WHERE label = 'Vérifier le propriétaire';
UPDATE audit_checklist_items SET label = 'Check the placeholder source' WHERE label = 'Vérifier la source placeholder';

UPDATE tasks SET title = 'Clarify required columns' WHERE title = 'Clarifier les colonnes obligatoires';
UPDATE tasks SET instructions = 'Demo blocking intervention' WHERE instructions = 'Intervention bloquante de démonstration';

UPDATE issues SET
    title = 'Sample minor issue',
    description = 'An editorial metadata field must be corrected.',
    suggestions = 'Prepare edit 1.1'
WHERE title = 'Exemple de signalement mineur';

UPDATE issues SET
    title = 'Sample major change',
    description = 'The business process changed structurally.',
    suggestions = 'Prepare version 2.0'
WHERE title = 'Exemple de changement majeur';

UPDATE flags SET reason = 'Audit due date reached' WHERE reason = 'Échéance d''audit atteinte';
UPDATE flags SET reason = 'Audit due date exceeded' WHERE reason = 'Échéance d''audit dépassée';

UPDATE notifications SET title = 'Audit due' WHERE title = 'Audit à réaliser';
UPDATE notifications SET body = replace(body, 'L''audit de ', 'The audit for ') WHERE body LIKE 'L''audit de %';
UPDATE notifications SET body = replace(body, ' est arrivé à échéance.', ' is due.') WHERE body LIKE '% est arrivé à échéance.';
UPDATE notifications SET title = 'Decision required' WHERE title = 'Décision attendue';
UPDATE notifications SET title = 'Corrections requested' WHERE title = 'Corrections demandées';
UPDATE notifications SET title = 'Review approved' WHERE title = 'Avis de review favorable';
UPDATE notifications SET body = replace(body, 'La proposition ', 'Proposal ') WHERE body LIKE 'La proposition %';
UPDATE notifications SET body = replace(body, ' a reçu un avis favorable.', ' received a favorable review.') WHERE body LIKE '% a reçu un avis favorable.';
UPDATE notifications SET title = 'Publication approved' WHERE title = 'Publication validée';
UPDATE notifications SET body = replace(body, ' publiée dans ', ' published in ') WHERE body LIKE 'Version % publiée dans %';
UPDATE notifications SET title = 'Review validated' WHERE title = 'Review validée';
UPDATE notifications SET body = 'The version number remains unchanged.' WHERE body = 'Le numéro de version reste inchangé.';
UPDATE notifications SET title = 'Issue to decide' WHERE title = 'Signalement à décider';
UPDATE notifications SET title = 'Task assigned' WHERE title = 'Tâche attribuée';

UPDATE activity_logs SET reason = 'Demo seed' WHERE reason = 'Seed de démonstration';
UPDATE activity_logs SET reason = replace(reason, 'Seed restreint version ', 'Restricted seed version ') WHERE reason LIKE 'Seed restreint version %';
UPDATE validation_events SET reason = 'Review validated without changes' WHERE reason = 'Review validée sans modification';
