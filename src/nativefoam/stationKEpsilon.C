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

#include "stationKEpsilon.H"
#include "fvOptions.H"
#include "bound.H"
#include "fvcGrad.H"
#include "fvcDiv.H"
#include "fvcMeshPhi.H"
#include "fvmDdt.H"
#include "fvmDiv.H"
#include "fvmLaplacian.H"
#include "fvmSup.H"

namespace Foam
{
namespace RASModels
{
template<class BasicMomentumTransportModel>
void stationKEpsilon<BasicMomentumTransportModel>::correct()
{
    if (!this->turbulence_)
    {
        return;
    }

    // Local references
    const alphaField& alpha = this->alpha_;
    const rhoField& rho = this->rho_;
    const surfaceScalarField& alphaRhoPhi = this->alphaRhoPhi_;
    const volVectorField& U = this->U_;
    volScalarField& nut = this->nut_;
    fv::options& fvOptions(fv::options::New(this->mesh_));

    eddyViscosity<RASModel<BasicMomentumTransportModel>>::correct();

    volScalarField::Internal divU
    (
        fvc::div(fvc::absolute(this->phi(), U))()
    );

    tmp<volTensorField> tgradU = fvc::grad(U);
    volScalarField::Internal G
    (
        this->GName(),
        nut()*(dev(twoSymm(tgradU().v())) && tgradU().v())
    );
    tgradU.clear();

    const scalarField epsilonInitialGuess(this->epsilon_.primitiveField());

    // Update epsilon and G at the wall
    this->epsilon_.boundaryFieldRef().updateCoeffs();

    // Dissipation equation
    tmp<fvScalarMatrix> epsEqn
    (
        fvm::ddt(alpha, rho, this->epsilon_)
      + fvm::div(alphaRhoPhi, this->epsilon_)
      - fvm::laplacian(alpha*rho*this->DepsilonEff(), this->epsilon_)
     ==
        this->C1_*alpha()*rho()*G*this->epsilon_()/this->k_()
      - fvm::SuSp(((2.0/3.0)*this->C1_ - this->C3_)*alpha()*rho()*divU, this->epsilon_)
      - fvm::Sp(this->C2_*alpha()*rho()*this->epsilon_()/this->k_(), this->epsilon_)
      + this->epsilonSource()
    );

    epsEqn.ref().relax();
    fvOptions.constrain(epsEqn.ref());
    epsEqn.ref().boundaryManipulate(this->epsilon_.boundaryFieldRef());
    epsEqn.ref() -= fvOptions(alpha, rho, this->epsilon_);
    this->epsilon_.primitiveFieldRef() = epsilonInitialGuess;
    solve(epsEqn);
    fvOptions.correct(this->epsilon_);
    bound(this->epsilon_, this->epsilonMin_);

    // Turbulent kinetic energy equation
    tmp<fvScalarMatrix> kEqn
    (
        fvm::ddt(alpha, rho, this->k_)
      + fvm::div(alphaRhoPhi, this->k_)
      - fvm::laplacian(alpha*rho*this->DkEff(), this->k_)
     ==
        alpha()*rho()*G
      - fvm::SuSp((2.0/3.0)*alpha()*rho()*divU, this->k_)
      - fvm::Sp(alpha()*rho()*this->epsilon_()/this->k_(), this->k_)
      + this->kSource()
      + fvOptions(alpha, rho, this->k_)
    );

    kEqn.ref().relax();
    fvOptions.constrain(kEqn.ref());
    solve(kEqn);
    fvOptions.correct(this->k_);
    bound(this->k_, this->kMin_);

    this->correctNut();
}


}
}
