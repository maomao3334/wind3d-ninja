/*---------------------------------------------------------------------------*\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     | Website:  https://openfoam.org
    \\  /    A nd           | Copyright (C) 2011-2020 OpenFOAM Foundation
     \\/     M anipulation  |
-------------------------------------------------------------------------------
License
    This file is part of OpenFOAM.

    OpenFOAM is free software: you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OpenFOAM is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
    FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
    for more details.

    You should have received a copy of the GNU General Public License
    along with OpenFOAM.  If not, see <http://www.gnu.org/licenses/>.

\*---------------------------------------------------------------------------*/

#include "stationNudging.H"
#include "fvMesh.H"
#include "fvMatrices.H"
#include "fvmSup.H"
#include "addToRunTimeSelectionTable.H"
#include <cmath>

// * * * * * * * * * * * * * * Static Data Members * * * * * * * * * * * * * //

namespace Foam
{
    namespace fv
    {
        defineTypeNameAndDebug(stationNudging, 0);

        addToRunTimeSelectionTable
        (
            cellSetOption,
            stationNudging,
            dictionary
        );
    }

    template<>
    const char* NamedEnum<fv::stationNudging::volumeMode, 2>::names[] =
    {
        "absolute",
        "specific"
    };
}

const Foam::NamedEnum<Foam::fv::stationNudging::volumeMode, 2>
    Foam::fv::stationNudging::volumeModeNames_;


// * * * * * * * * * * * ** Private Member Functions  ** * * * * * * * * * * //

template<class Type>
void Foam::fv::stationNudging::addSupType
(
    fvMatrix<Type>& eqn,
    const label fieldi
)
{
    if (debug)
    {
        Info<< "stationNudging<" << pTraits<Type>::typeName
            << ">::addSup for source " << name_ << endl;
    }

    const scalar t = mesh_.time().value();

    const GeometricField<Type, fvPatchField, volMesh>& psi = eqn.psi();

    typename GeometricField<Type, fvPatchField, volMesh>::Internal Su
    (
        IOobject
        (
            name_ + fieldNames_[fieldi] + "Su",
            mesh_.time().timeName(),
            mesh_,
            IOobject::NO_READ,
            IOobject::NO_WRITE
        ),
        mesh_,
        dimensioned<Type>
        (
            "zero",
            eqn.dimensions()/dimVolume,
            Zero
        ),
        false
    );

    // Explicit source function for the field
    UIndirectList<Type>(Su, cells_) = fieldSu_[fieldi].value<Type>(t)/VDash_;

    volScalarField::Internal Sp
    (
        IOobject
        (
            name_ + fieldNames_[fieldi] + "Sp",
            mesh_.time().timeName(),
            mesh_,
            IOobject::NO_READ,
            IOobject::NO_WRITE
        ),
        mesh_,
        dimensioned<scalar>
        (
            "zero",
            Su.dimensions()/psi.dimensions(),
            0
        ),
        false
    );

    // Implicit source function for the field
    UIndirectList<scalar>(Sp, cells_) = fieldSp_[fieldi].value(t)/VDash_;

    const scalar implicit = fieldSp_[fieldi].value(t);
    if (!std::isfinite(implicit) || implicit > 0)
    {
        FatalErrorInFunction << "stationNudging implicit coefficient must be finite and <= 0" << exit(FatalError);
    }
    forAll(cells_, index)
    {
        for (direction cmpt = 0; cmpt < pTraits<Type>::nComponents; ++cmpt)
        {
            if (!std::isfinite(component(Su[cells_[index]], cmpt)))
            {
                FatalErrorInFunction << "stationNudging explicit coefficient must be finite" << exit(FatalError);
            }
        }
    }
    eqn += Su - fvm::Sp(-Sp, psi);
}


template<class Type>
void Foam::fv::stationNudging::addSupType
(
    const volScalarField& rho,
    fvMatrix<Type>& eqn,
    const label fieldi
)
{
    if (debug)
    {
        Info<< "stationNudging<" << pTraits<Type>::typeName
            << ">::addSup for source " << name_ << endl;
    }

    return this->addSup(eqn, fieldi);
}


// * * * * * * * * * * * * * * * * Constructors  * * * * * * * * * * * * * * //

Foam::fv::stationNudging::stationNudging
(
    const word& name,
    const word& modelType,
    const dictionary& dict,
    const fvMesh& mesh
)
:
    cellSetOption(name, modelType, dict, mesh),
    volumeMode_(volumeMode::absolute),
    VDash_(1)
{
    read(dict);
}


// * * * * * * * * * * * * * * * * Destructor  * * * * * * * * * * * * * * * //

Foam::fv::stationNudging::~stationNudging()
{}


// * * * * * * * * * * * * * * * Member Functions  * * * * * * * * * * * * * //

void Foam::fv::stationNudging::addSup
(
    fvMatrix<scalar>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    fvMatrix<vector>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    fvMatrix<symmTensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    fvMatrix<sphericalTensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    fvMatrix<tensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& rho,
    fvMatrix<scalar>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& rho,
    fvMatrix<vector>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& rho,
    fvMatrix<symmTensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& rho,
    fvMatrix<sphericalTensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& rho,
    fvMatrix<tensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& alpha,
    const volScalarField& rho,
    fvMatrix<scalar>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& alpha,
    const volScalarField& rho,
    fvMatrix<vector>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& alpha,
    const volScalarField& rho,
    fvMatrix<symmTensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& alpha,
    const volScalarField& rho,
    fvMatrix<sphericalTensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


void Foam::fv::stationNudging::addSup
(
    const volScalarField& alpha,
    const volScalarField& rho,
    fvMatrix<tensor>& eqn,
    const label fieldi
)
{
    addSupType(eqn, fieldi);
}


bool Foam::fv::stationNudging::read(const dictionary& dict)
{
    if (cellSetOption::read(dict))
    {
        volumeMode_ = volumeModeNames_.read(coeffs_.lookup("volumeMode"));

        const dictionary& sources = coeffs_.subDict("sources");

        // Number of fields with a source term
        const label nFields = sources.size();

        // Set field names and source terms
        fieldNames_.setSize(nFields);
        fieldSp_.setSize(nFields);
        fieldSu_.setSize(nFields);
        label i = 0;
        forAllConstIter(dictionary, sources, iter)
        {
            fieldNames_[i] = iter().keyword();
            fieldSu_.set
            (
                i,
                objectFunction1::New<VolField>
                (
                    "explicit",
                    iter().dict(),
                    fieldNames_[i],
                    mesh_
                ).ptr()
            );
            fieldSp_.set
            (
                i,
                Function1<scalar>::New
                (
                    "implicit",
                    iter().dict()
                ).ptr()
            );
            const scalar implicit = fieldSp_[i].value(mesh_.time().value());
            if (!std::isfinite(implicit) || implicit > 0)
            {
                FatalIOErrorInFunction(dict) << "stationNudging implicit coefficient must be finite and <= 0" << exit(FatalIOError);
            }
            i++;
        }

        // Set volume normalisation
        VDash_ = 1;
        if (volumeMode_ == volumeMode::absolute)
        {
            VDash_ = V_;
        }

        if (!std::isfinite(VDash_) || VDash_ <= 0)
        {
            FatalIOErrorInFunction(dict) << "stationNudging volume normalisation must be positive and finite" << exit(FatalIOError);
        }
        applied_.setSize(nFields, false);

        return true;
    }
    else
    {
        return false;
    }
}

// ************************************************************************* //
