from .extensions import db
from .models import AuditLog

def audit(org_id, actor, action, entity_id="", details=None):
    db.session.add(AuditLog(org_id=org_id, actor=actor, action=action,
                            entity_id=str(entity_id), details=details))
    db.session.commit()