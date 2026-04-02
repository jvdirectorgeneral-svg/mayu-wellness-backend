from pydantic import BaseModel

class AmbassadorRegister(BaseModel):
    name: str
    email: str
    password: str
    national_id: str
    address: str
    bank_name: str
    account_type: str
    bank_account_number: str


class AmbassadorLogin(BaseModel):
    email: str
    password: str
